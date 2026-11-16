"""Isolated, offline Jev consolidation lifecycle comparison.

Uses the real Switchyard skill pre_llm_call hook, the real effort controller's
llm_request middleware, and the existing evaluation-only consolidation proxy.
Both arms use a strictly in-process Jev transport fixture, never credentials
or HTTPS. Hook calls emulate Hermes; no Hermes gateway/agent is started.
"""
from __future__ import annotations

import http.client
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

import hermes_switchyard
from hermes_switchyard import automatic, egress_redaction
from hermes_switchyard.client import DecisionClient, EXPECTED_MODEL
from hermes_switchyard.reasoning_effort_adapter import ReasoningEffortController
from hermes_switchyard.two_stage_routing import TwoStageConfig

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "hermes_switchyard._combined_lifecycle_fixture",
    ROOT / "evaluation/turn_consolidation/pilot.py",
)
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


class FakeHost:
    """Only the exact two Hermes plugin registration seams under test."""

    def __init__(self):
        self.hooks = []
        self.middleware = []

    def register_hook(self, name, callback):
        if name == "pre_llm_call":
            self.hooks.append(callback)

    def register_middleware(self, name, callback):
        if name == "llm_request":
            self.middleware.append(callback)


class FixtureTransport:
    """Closed-set, deterministic Jev response data; no HTTP client."""

    def __init__(self, *, needs_skill=True, request_id=True, fail=False):
        self.calls = []
        self.needs_skill = needs_skill
        self.request_id = request_id
        self.fail = fail

    @staticmethod
    def choice(criteria, selected):
        assert selected in criteria, (selected, tuple(criteria))
        other = [key for key in criteria if key != selected]
        probabilities = {
            key: (1.0 if not other else 0.99) if key == selected
            else 0.01 / len(other)
            for key in criteria
        }
        return {
            "choice": selected,
            "confidence": max(probabilities.values()),
            "probabilities": probabilities,
        }

    def __call__(self, payload):
        assert set(payload) == {"model", "state", "questions", "provider"}
        assert payload["provider"] == {"allow_fallbacks": False}
        # Deliberately retain only synthetic request fields in test memory.
        self.calls.append({
            "state": payload["state"],
            "questions": tuple(payload["questions"]),
        })
        if self.fail:
            raise RuntimeError("fixture failure")
        answers = {}
        for name, question in payload["questions"].items():
            if question["type"] == "choice":
                selected = (
                    "csv-skill" if name.startswith("skill")
                    else "low"
                )
                answers[name] = self.choice(question["criteria"], selected)
            elif question["type"] == "noul":
                answers[name] = {
                    "noul": (
                        0.99 if self.needs_skill else 0.01
                    ) if name == "needs_skill" else 0.01
                }
            else:
                raise AssertionError("Unexpected fixture question")
        result = {
            "model": EXPECTED_MODEL,
            "answers": answers,
            "usage": {},
            "latency_ms": 0.1,
        }
        if self.request_id:
            result["request_id"] = f"fixture-{len(self.calls)}"
        return result


class CombinedLifecycleOfflineTests(unittest.TestCase):
    TASK = "Please explain this public synthetic CSV example clearly."
    CANDIDATES = (
        {"name": "csv-skill", "description": "Analyze public CSV data"},
        {"name": "log-skill", "description": "Analyze public log data"},
    )

    def setUp(self):
        self.patchers = [
            mock.patch.object(automatic, "_persist_receipt", return_value=True),
            mock.patch.object(
                automatic.receipt_history, "record_turn_receipt", return_value=None
            ),
            mock.patch.object(
                automatic, "discover_available_skill_candidates",
                return_value=(),
            ),
            mock.patch.object(
                http.client.HTTPSConnection, "connect",
                side_effect=AssertionError("External HTTPS is forbidden"),
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        egress_redaction._reset_for_tests(lambda text: text, loaded=True)
        self.addCleanup(egress_redaction._reset_for_tests)

    def exercise(
        self, *, combined, task=None, needs_skill=True,
        request_id=True, fail=False, session="public-session",
        turn="public-turn",
    ):
        task = self.TASK if task is None else task
        host = FakeHost()
        provider = FixtureTransport(
            needs_skill=needs_skill, request_id=request_id, fail=fail
        )
        loaded = []
        records = []
        original_plugin_client = hermes_switchyard.DecisionClient
        try:
            owner = pilot.ContextProxy(host) if combined else host
            def make_client(cls=DecisionClient):
                return cls(
                    api_key="synthetic-transport-only",
                    model=EXPECTED_MODEL,
                    transport=provider,
                    timeout=0.4,
                )
            controller = ReasoningEffortController(
                client_factory=make_client,
                allowed_efforts=("low", "medium", "high"),
                deadline_seconds=0.4,
                public_or_sanitized_data_ack=True,
                session_env=lambda _name: "",
                record_decision=records.append,
                receipt_mode="off",
            )
            self.addCleanup(controller.close)
            skill_hook = automatic.build_pre_llm_call_hook(
                configured_candidates=self.CANDIDATES,
                routing_mode="hosted_sanitized",
                hosted_mode="always",
                public_or_sanitized_data_ack=True,
                client_factory=lambda: make_client(
                    pilot.JointClient if combined else DecisionClient
                ),
                consumer_mode="load",
                skill_loader=lambda name, task_id=None: (
                    loaded.append((name, task_id)) or f"PUBLIC SKILL: {name}"
                ),
                two_stage=TwoStageConfig(enabled=False),
                light_turn_bypass=False,
                cache_seconds=0,
            )
            assert skill_hook is not None
            owner.register_hook(
                "pre_llm_call", controller.build_pre_llm_call_hook()
            )
            owner.register_hook("pre_llm_call", skill_hook)
            owner.register_middleware("llm_request", controller.on_llm_request)
            assert len(host.hooks) == 2 and len(host.middleware) == 1
            ctx = {
                "session_id": session,
                "task_id": "task-" + session,
                "turn_id": turn,
                "user_message": task,
                "parent_session_id": None,
                "model": "gpt-6-sol",
                "platform": "cli",
            }
            responses = [callback(**ctx) for callback in host.hooks]
            request = {
                "model": "gpt-6-sol",
                "input": [{"role": "user", "content": task}],
                "reasoning": {"effort": "high", "summary": "auto"},
            }
            changed = host.middleware[0](
                request=request,
                session_id=ctx["session_id"],
                task_id=ctx["task_id"],
                turn_id=ctx["turn_id"],
                provider="openai-codex",
                api_mode="codex_responses",
                model="gpt-6-sol",
            )
            wire = changed["request"] if changed is not None else request
            return {
                "calls": provider.calls,
                "loads": loaded,
                "skill_metadata": responses[-1]["metadata"]["skill_recommendation"],
                "effort": wire["reasoning"]["effort"],
                "wire": wire,
                "records": records,
                "shared_receipts": (
                    list(owner.broker.receipts) if combined else []
                ),
                "unconsumed": (
                    list(owner.broker.records) if combined else []
                ),
            }
        finally:
            # ContextProxy changes only this test process's imported symbol.
            hermes_switchyard.DecisionClient = original_plugin_client

    def test_combined_path_loads_same_skill_and_sends_same_effort_in_one_call(self):
        ordinary = self.exercise(combined=False)
        shared = self.exercise(combined=True)
        for outcome in (ordinary, shared):
            self.assertEqual(outcome["loads"], [("csv-skill", "public-session")])
            self.assertEqual(outcome["skill_metadata"]["status"], "loaded")
            self.assertEqual(outcome["effort"], "low")
            self.assertEqual(outcome["wire"]["model"], "gpt-6-sol")
            self.assertTrue(outcome["records"])
        self.assertEqual(len(ordinary["calls"]), 2)
        self.assertEqual(len(shared["calls"]), 1)
        self.assertEqual(len(shared["shared_receipts"]), 1)
        self.assertEqual(shared["unconsumed"], [])
        self.assertEqual(
            set(shared["calls"][0]["questions"]),
            {"skill", "needs_skill", "reasoning_effort_medium",
             "reasoning_effort_high", "stakes"},
        )

    def test_hosted_no_skill_abstains_without_loading_in_both_arms(self):
        ordinary = self.exercise(combined=False, needs_skill=False)
        shared = self.exercise(combined=True, needs_skill=False)
        for outcome in (ordinary, shared):
            self.assertEqual(outcome["loads"], [])
            self.assertEqual(outcome["skill_metadata"]["status"], "abstained")
            self.assertEqual(outcome["effort"], "low")
        self.assertEqual(len(ordinary["calls"]), 2)
        self.assertEqual(len(shared["calls"]), 1)

    def test_marked_confidential_input_never_reaches_fixture_transport(self):
        for combined in (False, True):
            with self.subTest(combined=combined):
                outcome = self.exercise(
                    combined=combined,
                    task="Company Confidential\nPrivate synthetic fixture.",
                )
                self.assertEqual(outcome["calls"], [])
                self.assertEqual(outcome["shared_receipts"], [])
                self.assertEqual(outcome["effort"], "high")

    def test_missing_request_identity_does_not_fake_savings(self):
        ordinary = self.exercise(combined=False, request_id=False)
        shared = self.exercise(combined=True, request_id=False)
        self.assertEqual(len(ordinary["calls"]), 2)
        self.assertEqual(len(shared["calls"]), 2)
        self.assertEqual(shared["shared_receipts"], [])
        self.assertEqual(shared["effort"], ordinary["effort"])

    def test_fixture_failure_keeps_host_effort_and_does_not_claim_shared_call(self):
        for combined in (False, True):
            with self.subTest(combined=combined):
                outcome = self.exercise(combined=combined, fail=True)
                self.assertEqual(outcome["effort"], "high")
                self.assertEqual(outcome["shared_receipts"], [])
                self.assertGreaterEqual(len(outcome["calls"]), 1)

    def test_distinct_turns_do_not_share_accounting(self):
        first = self.exercise(
            combined=True, session="first-public-session", turn="turn-1"
        )
        second = self.exercise(
            combined=True, session="second-public-session", turn="turn-2"
        )
        self.assertEqual(len(first["calls"]), 1)
        self.assertEqual(len(second["calls"]), 1)
        self.assertEqual(len(first["shared_receipts"]), 1)
        self.assertEqual(len(second["shared_receipts"]), 1)


if __name__ == "__main__":
    unittest.main()
