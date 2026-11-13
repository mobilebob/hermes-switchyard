"""Qualify actual-client binding independently of model/platform eligibility."""
from __future__ import annotations

from unittest import mock

import hermes_switchyard as switchyard
from hermes_switchyard import egress_redaction
from hermes_switchyard.client import DecisionClient, DEFAULT_ENDPOINT, EXPECTED_MODEL
from hermes_switchyard.combined_decisions import SharedDecisionBroker, SharedSkillClient, wrap_skill_pre_hook
from test_jev_combined_lifecycle_offline import FixtureTransport
import test_shared_jev_activation as activation
from test_shared_jev_activation import RecordingContext
from test_support import HermesHomeTestCase


class SharedJevIdentityTests(HermesHomeTestCase):
    TASK = "Please explain this public synthetic CSV example."
    KEY = ("public-session", "public-task", "public-turn")

    def setUp(self):
        super().setUp()
        egress_redaction._reset_for_tests(lambda text: text, loaded=True)
        self.addCleanup(egress_redaction._reset_for_tests)
        self.credentials = {"typesafe": "fixture-generation-a", "openrouter": "fixture-other"}
        secret = mock.patch.object(switchyard, "_secret", side_effect=lambda provider="auto": self.credentials[provider])
        secret.start()
        self.addCleanup(secret.stop)
        self.context = RecordingContext({
            **activation.SharedJevActivationTests.ELIGIBLE,
            "jev_provider": "openrouter", "jev_model": EXPECTED_MODEL,
        })
        brokers = []

        def capture(**kwargs):
            broker = SharedDecisionBroker(**kwargs)
            brokers.append(broker)
            return broker

        with mock.patch.object(switchyard, "SharedDecisionBroker", side_effect=capture):
            switchyard.register(self.context)
        self.assertEqual(len(brokers), 1)
        self.broker = brokers[0]
        self.fixture = FixtureTransport()
        self.change_inflight = None

        def transport(payload):
            result = self.fixture(payload)
            if self.change_inflight is not None:
                self.change_inflight()
            return result

        self.client = DecisionClient(api_key=self.credentials["openrouter"], endpoint=DEFAULT_ENDPOINT,
                                     model=EXPECTED_MODEL, transport=transport)
        self.addCleanup(self.client.close)
        self.facade = SharedSkillClient(self.client, self.broker)

    def decide(self):
        hook = wrap_skill_pre_hook(lambda **_: self.facade.decide(
            {"task": self.TASK, "stage": 1},
            {"skill": {"type": "choice", "instructions": "Select a public skill",
                       "criteria": {"csv-skill": "CSV", "log-skill": "Logs"}},
             "needs_skill": {"type": "noul", "instructions": "Does this task need a public skill?"}},
            public_or_sanitized_data_ack=True,
        ), self.broker)
        result = hook(session_id=self.KEY[0], task_id=self.KEY[1], turn_id=self.KEY[2],
                      user_message=self.TASK, platform="cli", model="gpt-6-sol", parent_session_id=None)
        self.assertEqual(set(result["answers"]), {"skill", "needs_skill"})
        self.assertEqual(result["answers"]["skill"]["choice"], "csv-skill")
        self.assertEqual(len(self.fixture.calls), 1)
        return result

    def test_registered_client_binding_matches_unchanged_route(self):
        self.decide()
        self.assertIn(self.KEY, self.broker.records)
        self.assertIn("reasoning_effort_high", self.fixture.calls[0]["questions"])

    def test_client_created_before_credential_rotation_cannot_be_relabelled(self):
        self.credentials["openrouter"] = "fixture-generation-b"
        self.decide()
        self.assertEqual(self.broker.records, {})
        self.assertEqual(set(self.fixture.calls[0]["questions"]), {"skill", "needs_skill"})

    def test_client_created_before_model_change_cannot_be_relabelled(self):
        self.context.settings["jev_model"] = "jev-latest"
        self.decide()
        self.assertEqual(self.broker.records, {})
        self.assertEqual(set(self.fixture.calls[0]["questions"]), {"skill", "needs_skill"})

    def test_client_created_before_provider_change_cannot_be_relabelled(self):
        self.context.settings.update(jev_provider="typesafe", jev_model="jev-1.14.0")
        self.decide()
        self.assertEqual(self.broker.records, {})
        self.assertEqual(set(self.fixture.calls[0]["questions"]), {"skill", "needs_skill"})

    def test_inflight_credential_rotation_preserves_skill_but_discards_reuse(self):
        self.change_inflight = lambda: self.credentials.update(openrouter="fixture-generation-b")
        self.decide()
        self.assertEqual(self.broker.records, {})
        self.assertIn("reasoning_effort_high", self.fixture.calls[0]["questions"])

    def test_unavailable_actual_client_identity_uses_original_question_contract(self):
        delattr(self.client, "api_key")
        self.facade = SharedSkillClient(self.client, self.broker)
        self.decide()
        self.assertEqual(self.broker.records, {})
        self.assertEqual(set(self.fixture.calls[0]["questions"]), {"skill", "needs_skill"})
