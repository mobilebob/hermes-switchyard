"""Offline tests of passive model-decision reuse; no network/provider credentials."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
import unittest
from unittest import mock

from hermes_switchyard import egress_redaction
from hermes_switchyard.client import DecisionClient, EXPECTED_MODEL
from hermes_switchyard.model_policy import recommend_approved_model
from hermes_switchyard.model_routing_shadow import ModelRoutingShadow
from hermes_switchyard.native_provenance import NativeSnapshot


class SyntheticContractProvenance:
    """Synthetic policy contract only, explicitly NOT native qualification."""
    def profile(self):
        return (ModelRoutingShadow.digest("contract-fixture"), "fixture")

    def invalidate(self):
        pass

    def read(self, request, context):
        return NativeSnapshot(context, self.profile(), None, None,
                              source="synthetic-policy-contract", missing=())


class ShadowRoutingTests(unittest.TestCase):
    TASK = "Explain this public synthetic CSV example."

    def setUp(self):
        egress_redaction._reset_for_tests(lambda text: text, loaded=True)
        self.addCleanup(egress_redaction._reset_for_tests)
        self.rows = [{
            "id": "public-small", "provider": "openai-codex", "model": "gpt-6-luna",
            "account": "fixture-account", "approved": True, "cost": 1,
            "data_classes_allowed": ["public"], "tool_capabilities": [],
            "context_limit": 120000, "description": "Public synthetic tasks",
        }]
        self.version, self.expiry = "fixture-v1", "2100-01-01T00:00:00Z"
        self.identity = {"provider": "fixture-jev", "generation": "one"}
        self.calls = []
        self.context = {
            "session_id": "public-session", "task_id": "public-task", "turn_id": "public-turn",
            "provider": "openai-codex", "model": "gpt-6-sol", "model_account": "fixture-account",
            "manual_model_pinned": False, "platform": "cli", "parent_session_id": None,
        }
        self.request = {"model": "gpt-6-sol", "input": [{"role": "user", "content": self.TASK}], "reasoning": {"effort": "high"}}
        self.shadow = self.make()
        self.capture()

    def make(self, mode: object = "shadow"):
        return ModelRoutingShadow(
            mode=mode, identity_factory=lambda: self.identity,
            native_provenance=SyntheticContractProvenance(),
            registry_factory=lambda: (self.rows, self.version, self.expiry),
        )

    def capture(self, **context):
        self.shadow.capture(user_message=self.TASK, **{**self.context, **context})

    def decision(self, on_dispatch=None):
        def transport(payload):
            self.calls.append(payload)
            if on_dispatch:
                on_dispatch()
            return {"model": EXPECTED_MODEL, "request_id": "fixture-model-1", "usage": {},
                    "answers": {name: {"noul": 0.95} for name in payload["questions"]}}
        client = DecisionClient(api_key="fixture-key", model=EXPECTED_MODEL, transport=transport)
        return recommend_approved_model(
            task=self.TASK, requirements={"data_classes": ["public"]}, registry=self.rows,
            registry_version=self.version, valid_until=self.expiry, client=client,
            public_or_sanitized_data_ack=True,
        )

    def offer(self, **kwargs):
        ticket = self.shadow.begin(task=self.TASK, context=self.context, consent=True)
        result = self.decision(**kwargs)
        self.assertEqual(result["status"], "selected")
        self.shadow.offer(ticket, result)
        return ticket

    def observe(self, **context):
        before = copy.deepcopy(self.request)
        self.assertIsNone(self.shadow.observe(self.request, consent=True, **{**self.context, **context}))
        self.assertEqual(self.request, before)
        return self.shadow.receipts[-1]

    def test_reuses_one_real_validated_model_decision_without_a_new_call_or_mutation(self):
        self.offer()
        receipt = self.observe()
        self.assertEqual(receipt["status"], "proposed")
        self.assertEqual(receipt["proposed"]["model"], "gpt-6-luna")
        self.assertEqual(len(self.calls), 1)
        self.assertIs(receipt["applied"], False)
        self.assertIs(receipt["can_apply"], False)
        self.assertEqual(receipt["new_jev_requests"], 0)
        self.assertNotIn(self.TASK, json.dumps(receipt))

    def test_consume_once_and_concurrent_observers(self):
        self.offer()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.shadow.observe(self.request, consent=True, **self.context), range(8)))
        self.assertEqual(sum(row["status"] == "proposed" for row in self.shadow.receipts), 1)
        self.assertEqual(len(self.calls), 1)

    def test_manual_pin_and_unknown_pin_abstain_including_default_model_pin(self):
        for pin in (True, None, 0, "false"):
            with self.subTest(pin=pin):
                self.offer()
                receipt = self.observe(manual_model_pinned=pin)
                self.assertEqual(receipt["status"], "abstained")
                self.assertIn(receipt["reason"], {"manual_model_pin", "pin_provenance_unavailable"})

    def test_provider_account_and_active_model_must_match_trusted_context(self):
        for change in ({"provider": "another-provider"}, {"model_account": "another-account"}, {"model_account": None}, {"model": "different-model"}):
            with self.subTest(change=change):
                self.offer()
                self.assertEqual(self.observe(**change)["status"], "abstained")

    def test_unrelated_skill_or_effort_answers_cannot_become_model_selection(self):
        for unrelated in ({"answers": {"reasoning_effort": {"choice": "low"}}}, {"status": "loaded", "selected": "public-small"}):
            ticket = self.shadow.begin(task=self.TASK, context=self.context, consent=True)
            self.shadow.offer(ticket, unrelated)
            self.assertEqual(self.observe()["reason"], "decision_unavailable")
        self.assertEqual(self.calls, [])

    def test_cancellation_during_dispatch_drops_late_response(self):
        self.offer(on_dispatch=self.shadow.invalidate)
        self.assertEqual(self.observe()["status"], "abstained")

    def test_cancellation_after_offer_drops_pending_decision(self):
        self.offer()
        self.shadow.invalidate()
        self.assertEqual(self.observe()["status"], "abstained")

    def test_new_turn_and_overlapping_producer_supersede_old_ticket(self):
        ticket = self.shadow.begin(task=self.TASK, context=self.context, consent=True)
        self.capture()
        self.shadow.offer(ticket, self.decision())
        self.assertEqual(self.observe()["status"], "abstained")
        self.offer()
        self.capture(turn_id="new-turn")
        self.assertEqual(self.observe()["status"], "abstained")

    def test_identity_change_during_and_after_dispatch(self):
        self.offer(on_dispatch=lambda: self.identity.update(generation="changed"))
        self.assertEqual(self.observe()["status"], "abstained")
        self.offer()
        self.identity["generation"] = "changed-again"
        self.assertEqual(self.observe()["reason"], "identity_changed")

    def test_registry_change_revocation_and_expiry_reject_proposal(self):
        self.offer()
        self.rows[0]["approved"] = False
        self.assertEqual(self.observe()["reason"], "registry_changed")
        self.rows[0]["approved"] = True
        self.offer()
        self.expiry = "2000-01-01T00:00:00Z"
        self.assertEqual(self.observe()["reason"], "registry_changed")

    def test_timeout_and_stale_response(self):
        with mock.patch("hermes_switchyard.model_routing_shadow.time.monotonic", return_value=0):
            ticket = self.shadow.begin(task=self.TASK, context=self.context, consent=True)
        result = self.decision()
        with mock.patch("hermes_switchyard.model_routing_shadow.time.monotonic", return_value=1):
            self.shadow.offer(ticket, result)
        self.assertEqual(self.observe()["reason"], "decision_unavailable")
        self.offer()
        key = self.shadow.key(self.context)
        self.shadow.pending[key]["completed"] -= 31
        self.assertEqual(self.observe()["reason"], "expired")

    def test_off_and_invalid_mode_have_no_capture_receipts_or_calls(self):
        for mode in ("off", "invalid", None):
            self.shadow = self.make(mode)
            self.capture()
            self.assertIsNone(self.shadow.begin(task=self.TASK, context=self.context, consent=True))
            self.shadow.observe(self.request, consent=True, **self.context)
            self.assertEqual(len(self.shadow.receipts), 0)
        self.assertEqual(self.calls, [])

    def test_apply_mode_is_unavailable_and_does_not_mutate(self):
        self.shadow = self.make("apply")
        self.assertEqual(self.observe()["reason"], "apply_unavailable")
        self.assertEqual(self.calls, [])

    def test_privacy_consent_delegation_and_scope_gate_capture(self):
        self.assertIsNone(self.shadow.begin(task="Company Confidential\nPrivate synthetic task", context=self.context, consent=True))
        self.assertIsNone(self.shadow.begin(task=self.TASK, context=self.context, consent=False))
        for change in ({"parent_session_id": "parent"}, {"platform": "cron"}, {"task_id": None}):
            self.shadow.invalidate()
            self.capture(**change)
            self.assertIsNone(self.shadow.begin(task=self.TASK, context=self.context, consent=True))
        self.assertEqual(self.calls, [])

    def test_missing_request_id_and_multiple_requests_cannot_be_reused(self):
        for change in ({"request_id": None}, {"request_count": 2}, {"recommendation": {"id": "unapproved"}}):
            ticket = self.shadow.begin(task=self.TASK, context=self.context, consent=True)
            result = {**self.decision(), **change}
            self.shadow.offer(ticket, result)
            self.assertEqual(self.observe()["status"], "abstained")

    def test_context_or_tools_change_does_not_reuse_earlier_model_answer(self):
        for change in ({"tools": [{"name": "fixture-tool"}]}, {"previous_response_id": "old"}, {"input": [{"role": "user", "content": "Different public task"}]}):
            self.offer()
            original = self.request
            self.request = {**original, **change}
            self.assertEqual(self.observe()["reason"], "request_task_mismatch_or_unsupported")
            self.request = original

    def test_consumer_consent_is_rechecked_and_pending_decision_is_not_applied(self):
        self.offer()
        self.shadow.observe(self.request, consent=False, **self.context)
        self.assertEqual(self.shadow.receipts[-1]["reason"], "consent_required")
        self.assertEqual(self.observe()["status"], "abstained")

    def test_observer_failure_is_visible_and_invalidates_pending_work(self):
        self.offer()
        with mock.patch.object(self.shadow, "observe", side_effect=RuntimeError("fixture failure")):
            self.assertIsNone(self.shadow.safely("observe", self.request, consent=True, **self.context))
        self.assertEqual(self.shadow.status["last_receipt"]["reason"], "observer_failed")
        self.assertEqual(len(self.shadow.pending), 0)
        self.assertEqual(len(self.calls), 1)

    def test_missing_registry_or_identity_cannot_start_capture(self):
        self.shadow.identity_factory = lambda: None
        self.assertIsNone(self.shadow.begin(task=self.TASK, context=self.context, consent=True))
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
