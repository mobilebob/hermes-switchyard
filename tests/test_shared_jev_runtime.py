"""One focused offline regression for the *shipped opt-in* shared Jev path.

Uses the production SharedSkillClient/Broker, real automatic hook and effort
middleware, and DecisionClient's strict validator with an in-process fixture.
No main-model call, provider connection, or gateway change.
"""
from __future__ import annotations

import unittest
from unittest import mock

from hermes_switchyard import automatic, egress_redaction
from hermes_switchyard.client import DecisionClient, EXPECTED_MODEL
from hermes_switchyard.combined_decisions import (
    SharedDecisionBroker, SharedSkillClient, wrap_skill_pre_hook,
)
from hermes_switchyard.reasoning_effort_adapter import ReasoningEffortController
from hermes_switchyard.two_stage_routing import TwoStageConfig
from test_jev_combined_lifecycle_offline import FixtureTransport


class SharedJevRuntimeTests(unittest.TestCase):
    TASK = "Please explain this public synthetic CSV data example."

    def setUp(self):
        egress_redaction._reset_for_tests(lambda value: value, loaded=True)
        self.addCleanup(egress_redaction._reset_for_tests)
        p = mock.patch.object(automatic, "_persist_receipt", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(
            automatic.receipt_history, "record_turn_receipt", return_value=None
        )
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(
            automatic, "discover_available_skill_candidates", return_value=()
        )
        p.start()
        self.addCleanup(p.stop)

    def run_case(self, *, enabled, identity_change=False, request_id=True,
                 confidential=False):
        fixture = FixtureTransport(request_id=request_id)
        identity = {"route": "synthetic"}
        broker = SharedDecisionBroker(identity_factory=lambda: dict(identity))
        loads = []

        def client():
            return DecisionClient(
                api_key="test-transport-only", model=EXPECTED_MODEL,
                transport=fixture, timeout=0.4,
            )

        controller = ReasoningEffortController(
            client_factory=client,
            allowed_efforts=("low", "medium", "high"),
            public_or_sanitized_data_ack=True,
            shared_decisions=broker if enabled else None,
            deadline_seconds=0.4,
            session_env=lambda _name: "",
            receipt_mode="off",
        )
        self.addCleanup(controller.close)
        skill_hook = automatic.build_pre_llm_call_hook(
            configured_candidates=[
                {"name": "csv-skill", "description": "Analyze public CSV data"},
                {"name": "log-skill", "description": "Analyze public logs"},
            ],
            routing_mode="hosted_sanitized",
            consumer_mode="load",
            client_factory=(
                (lambda: SharedSkillClient(client(), broker))
                if enabled else client
            ),
            skill_loader=lambda name, task_id=None: (
                loads.append((name, task_id)) or "PUBLIC CSV SKILL MARKER"
            ),
            two_stage=TwoStageConfig(enabled=False),
            cache_seconds=0,
            light_turn_bypass=False,
        )
        assert skill_hook is not None
        if enabled:
            skill_hook = wrap_skill_pre_hook(skill_hook, broker)
        task = (
            "Company Confidential\nPrivate test example."
            if confidential else self.TASK
        )
        ctx = {
            "session_id": "test-session",
            "task_id": "test-task",
            "turn_id": "test-turn",
            "model": "gpt-6-sol",
            "platform": "cli",
            "parent_session_id": None,
            "user_message": task,
        }
        controller.build_pre_llm_call_hook()(**ctx)
        skill = skill_hook(**ctx)
        if identity_change:
            identity["route"] = "different-synthetic-route"

        request = {
            "model": "gpt-6-sol",
            "input": [{"role": "user", "content": task}],
            "reasoning": {"effort": "high", "summary": "auto"},
        }
        changed = controller.on_llm_request(
            request=request,
            session_id=ctx["session_id"],
            task_id=ctx["task_id"],
            turn_id=ctx["turn_id"],
            provider="openai-codex",
            model="gpt-6-sol",
            api_mode="codex_responses",
        )
        sent = changed["request"] if changed else request
        return {
            "calls": list(fixture.calls),
            "loads": loads,
            "skill": skill["metadata"]["skill_recommendation"],
            "effort": sent["reasoning"]["effort"],
            "model": sent["model"],
            "receipts": list(broker.receipts),
            "pending": list(broker.records),
        }

    def test_feature_on_shares_one_validated_request(self):
        actual = self.run_case(enabled=True)
        self.assertEqual(actual["loads"], [("csv-skill", "test-session")])
        self.assertEqual(actual["skill"]["status"], "loaded")
        self.assertEqual(actual["effort"], "low")
        self.assertEqual(actual["model"], "gpt-6-sol")
        self.assertEqual(len(actual["calls"]), 1)
        self.assertEqual(len(actual["receipts"]), 1)
        self.assertEqual(actual["pending"], [])

    def test_feature_off_preserves_two_independent_requests(self):
        actual = self.run_case(enabled=False)
        self.assertEqual(actual["loads"], [("csv-skill", "test-session")])
        self.assertEqual(actual["effort"], "low")
        self.assertEqual(len(actual["calls"]), 2)
        self.assertEqual(actual["receipts"], [])

    def test_route_identity_change_discards_saved_decision(self):
        actual = self.run_case(enabled=True, identity_change=True)
        self.assertEqual(actual["effort"], "low")
        self.assertEqual(len(actual["calls"]), 2)
        self.assertEqual(actual["receipts"], [])

    def test_missing_provider_request_id_disallows_sharing(self):
        actual = self.run_case(enabled=True, request_id=False)
        self.assertEqual(actual["effort"], "low")
        self.assertEqual(len(actual["calls"]), 2)
        self.assertEqual(actual["receipts"], [])

    def test_restricted_input_never_uses_shared_provider_data(self):
        actual = self.run_case(enabled=True, confidential=True)
        self.assertEqual(actual["calls"], [])
        self.assertEqual(actual["receipts"], [])
        self.assertEqual(actual["effort"], "high")


if __name__ == "__main__":
    unittest.main()
