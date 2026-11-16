"""Offline regression: do not reclassify an unchanged high-stakes cap on status flips."""

import unittest
from hermes_switchyard import egress_redaction
from hermes_switchyard.reasoning_effort_adapter import ReasoningEffortController
from tests.test_reasoning_effort_adapter import FakeClient

ROUTE = {
    "provider": "openai-codex",
    "model": "gpt-6.1-sol",
    "api_mode": "codex_responses",
}


class HighStakesCapCacheTests(unittest.TestCase):
    def setUp(self):
        egress_redaction._reset_for_tests(lambda text: text, loaded=True)
        self.records = []

    def tearDown(self):
        egress_redaction._reset_for_tests()

    def make(self, stakes=0.9, choice="low", **kwargs):
        client = FakeClient(stakes=stakes, choice=choice)
        c = ReasoningEffortController(
            enabled=True,
            public_or_sanitized_data_ack=True,
            client_factory=lambda: client,
            record_decision=self.records.append,
            **kwargs,
        )
        self.addCleanup(c.close)
        self.capture(c)
        return c, client

    def capture(self, c, text="Synthetic public diagnostic request.", turn="turn-1"):
        c.capture_user_message(text, session_id="session-1", turn_id=turn)

    def request(self, c, effort="high", turn="turn-1", route=None):
        route = route or ROUTE
        req = {
            "model": route["model"],
            "input": "FIXED_SYNTHETIC_BODY",
            "reasoning": {"effort": effort},
        }
        result = c.on_llm_request(req, session_id="session-1", turn_id=turn, **route)
        final = result["request"] if result else req
        self.assertEqual(final["input"], "FIXED_SYNTHETIC_BODY")
        return final["reasoning"]["effort"]

    def tool(self, c, failed):
        c.record_tool_outcome(
            {
                "tool": "read_file",
                "ok": not failed,
                "error": "fixture failure" if failed else None,
            },
            session_id="session-1",
        )

    def test_high_stakes_failure_reuses_confirmed_cap(self):
        c, client = self.make()
        self.assertEqual(self.request(c), "high")
        self.tool(c, True)
        self.assertEqual(self.request(c), "high")
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(self.records[-1]["jev_called"])

    def test_high_stakes_failure_recovery_loop_keeps_cap_with_one_ask(self):
        c, client = self.make()
        sent = [self.request(c)]
        for failed in [True, False, True, False, True, False]:
            self.tool(c, failed)
            sent.append(self.request(c))
        self.assertEqual(sent, ["high"] * 7)
        self.assertEqual(len(client.calls), 1)

    def test_low_stakes_redecision_policy_unchanged(self):
        c, client = self.make(stakes=0, choice="high")
        self.request(c)
        self.tool(c, True)
        self.request(c)
        self.tool(c, False)
        self.request(c)
        self.assertEqual(len(client.calls), 3)

    def test_changed_capture_same_turn_invalidates_reuse(self):
        c, client = self.make()
        self.request(c)
        self.capture(c, "Different synthetic request in the same host turn.")
        self.tool(c, True)
        self.request(c)
        self.assertEqual(len(client.calls), 2)

    def test_new_turn_invalidates_reuse(self):
        c, client = self.make()
        self.request(c)
        self.capture(c, turn="turn-2")
        self.request(c, turn="turn-2")
        self.assertEqual(len(client.calls), 2)

    def test_changed_model_invalidates_reuse(self):
        c, client = self.make()
        self.request(c)
        self.request(c, route={**ROUTE, "model": "other-codex-model"})
        self.assertEqual(len(client.calls), 2)

    def test_changed_route_invalidates_reuse(self):
        c, client = self.make()
        self.request(c)
        self.tool(c, True)
        self.request(c, route={**ROUTE, "provider": "other-codex-route"})
        self.assertEqual(len(client.calls), 2)

    def test_changed_cap_invalidates_reuse(self):
        c, client = self.make()
        self.request(c)
        self.tool(c, True)
        self.assertEqual(self.request(c, effort="medium"), "medium")
        self.assertEqual(len(client.calls), 2)

    def test_allow_raise_keeps_existing_reevaluation(self):
        c, client = self.make(allow_raise=True)
        self.request(c)
        self.tool(c, True)
        self.request(c)
        self.assertEqual(len(client.calls), 2)

    def test_pin_makes_no_extra_ask_and_preserves_host_level(self):
        c, client = self.make()
        self.request(c)
        c.set_mode("pinned", session_id="session-1")
        self.tool(c, True)
        self.assertEqual(self.request(c), "high")
        self.assertEqual(len(client.calls), 1)

    def test_missing_capture_does_not_reuse_old_privacy_context(self):
        c, client = self.make()
        self.request(c)
        c.discard_user_message(session_id="session-1")
        self.tool(c, True)
        self.assertEqual(self.request(c), "high")
        self.assertEqual(self.records[-1]["reason_code"], "kept_requested_no_task_text")

    def test_route_change_without_tool_invalidates(self):
        c, client = self.make()
        self.request(c)
        self.request(c, route={**ROUTE, "provider": "other-codex-route"})
        self.assertEqual(len(client.calls), 2)

    def test_api_mode_change_without_tool_invalidates(self):
        c, client = self.make()
        self.request(c)
        self.request(c, route={**ROUTE, "api_mode": "responses"})
        self.assertEqual(len(client.calls), 2)

    def test_changed_stakes_without_dirty_flag_invalidates(self):
        for stakes in [0.8, 0.2, None, float("nan"), float("inf")]:
            with self.subTest(stakes=stakes):
                c, client = self.make()
                self.request(c)
                c._state_for(session_id="session-1").turn_stakes = stakes
                self.request(c)
                self.assertEqual(len(client.calls), 2)

    def test_non_read_failure_is_not_optimized(self):
        for tool in ["write_file", "terminal", "unknown_tool"]:
            with self.subTest(tool=tool):
                c, client = self.make()
                self.request(c)
                c.record_tool_outcome(
                    {"tool": tool, "ok": False}, session_id="session-1"
                )
                self.request(c)
                self.assertEqual(len(client.calls), 2)

    def test_missing_route_identity_is_not_optimized(self):
        for key in ["provider", "api_mode"]:
            with self.subTest(key=key):
                c, client = self.make()
                route = {**ROUTE, key: None}
                self.request(c, route=route)
                self.tool(c, True)
                self.request(c, route=route)
                self.assertEqual(len(client.calls), 2)

    def test_privacy_opt_out_cannot_consume_old_choice(self):
        c, client = self.make()
        self.request(c)
        c.public_or_sanitized_data_ack = False
        self.tool(c, True)
        self.request(c)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(self.records[-1]["reason_code"], "kept_requested_ack_required")

    def test_restricted_new_capture_falls_back_without_call(self):
        c, client = self.make()
        self.request(c)
        self.capture(c, "Confidential:\nSynthetic restricted diagnostic.")
        self.tool(c, True)
        self.assertEqual(self.request(c), "high")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(
            self.records[-1]["reason_code"], "kept_requested_restricted_text"
        )

    def test_new_scope_does_not_share_choice(self):
        c, client = self.make()
        self.request(c)
        c.capture_user_message(
            "Synthetic independent request.", session_id="session-2", turn_id="turn-1"
        )
        req = {
            "model": ROUTE["model"],
            "input": "FIXED_SYNTHETIC_BODY",
            "reasoning": {"effort": "high"},
        }
        c.on_llm_request(req, session_id="session-2", turn_id="turn-1", **ROUTE)
        self.assertEqual(len(client.calls), 2)

    def test_uncertain_new_decision_failure_retains_host_cap(self):
        c, client = self.make()
        self.request(c)
        self.capture(c, "Different synthetic diagnostic request.")
        client.error = RuntimeError("synthetic transport failure")
        self.assertEqual(self.request(c), "high")
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(
            self.records[-1]["reason_code"], "kept_requested_on_jev_failure"
        )

    def test_entire_outgoing_request_unchanged(self):
        import copy

        c, client = self.make()
        req = {
            "model": ROUTE["model"],
            "input": [{"role": "user", "content": "SYNTHETIC_REQUEST_BODY"}],
            "reasoning": {"effort": "high", "summary": "auto"},
            "tools": [{"type": "function", "name": "synthetic"}],
            "max_output_tokens": 1024,
            "store": False,
            "metadata": {"fixture": "same"},
        }
        original = copy.deepcopy(req)
        first = c.on_llm_request(req, session_id="session-1", turn_id="turn-1", **ROUTE)
        self.tool(c, True)
        second = c.on_llm_request(
            req, session_id="session-1", turn_id="turn-1", **ROUTE
        )
        self.assertEqual(first["request"] if first else req, original)
        self.assertEqual(second["request"] if second else req, original)
        self.assertEqual(req, original)
        self.assertEqual(len(client.calls), 1)
        provider_state = repr(client.calls)
        self.assertNotIn("SYNTHETIC_REQUEST_BODY", provider_state)
        self.assertNotIn("synthetic transport failure", provider_state)


if __name__ == "__main__":
    unittest.main()
