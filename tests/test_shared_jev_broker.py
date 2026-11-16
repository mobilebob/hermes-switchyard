"""Offline safety gates for the shipped broker, not the evaluation prototype."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest
from unittest import mock

from hermes_switchyard import egress_redaction
from hermes_switchyard.client import DecisionClient, EXPECTED_MODEL
from hermes_switchyard.combined_decisions import SharedDecisionBroker, _effort_parts
from test_jev_combined_lifecycle_offline import FixtureTransport


class SharedJevBrokerTests(unittest.TestCase):
    KEY = ("public-session", "public-task", "public-turn")
    TASK = "Please explain this public synthetic CSV example."

    def setUp(self):
        egress_redaction._reset_for_tests(lambda value: value, loaded=True)
        self.addCleanup(egress_redaction._reset_for_tests)
        self.identity = {"route": "public-fixture"}
        self.broker = SharedDecisionBroker(identity_factory=lambda: self.identity)
        self.controller = SimpleNamespace(
            public_or_sanitized_data_ack=True, deadline_seconds=0.4,
        )
        state, high = _effort_parts(self.TASK, "high")
        _, medium = _effort_parts(self.TASK, "medium")
        fixture = FixtureTransport()
        client = DecisionClient(
            api_key="fixture-key", model=EXPECTED_MODEL, transport=fixture,
        )
        self.response = client.decide(state, {
            "reasoning_effort_high": high["reasoning_effort"],
            "reasoning_effort_medium": medium["reasoning_effort"],
            "stakes": high["stakes"],
        }, public_or_sanitized_data_ack=True)
        self.capture = {"excerpt": self.TASK, "scan_reason": None, "metadata": None}

    def store(self, key=None, *, response=None, elapsed_ms=1):
        self.broker.store(
            self.KEY if key is None else key, self.TASK,
            self.response if response is None else response, elapsed_ms,
            request_identity=self.broker._route_identity(),
            generation=self.broker.begin(self.KEY if key is None else key),
        )

    def consume(self, key=None, *, capture=None, cap="high", candidates=None):
        return self.broker.consume(
            self.KEY if key is None else key,
            self.capture if capture is None else capture, cap,
            (["low", "medium", "high"] if candidates is None else candidates),
            self.controller,
        )

    def test_one_consumer_owns_one_shared_receipt(self):
        self.store()
        first = self.consume()
        assert first is not None
        self.assertEqual(first["effort"], "low")
        self.assertIs(first["jev_called"], False)
        self.assertIs(first["shared_decision"], True)
        self.assertIsNone(self.consume())
        self.assertEqual(len(self.broker.receipts), 1)
        self.assertEqual(self.broker.receipts[0]["shared_request_id"], "fixture-1")

    def test_concurrent_consumers_do_not_duplicate_receipt(self):
        self.store()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.consume(), range(16)))
        self.assertEqual(sum(value is not None for value in results), 1)
        self.assertEqual(len(self.broker.receipts), 1)

    def test_other_session_task_or_turn_cannot_consume_record(self):
        self.store()
        for index in range(3):
            key: list[str] = list(self.KEY)
            key[index] = "different-public-scope"
            self.assertIsNone(self.consume(tuple(key)))
        self.assertIsNotNone(self.consume())

    def test_privacy_metadata_consent_and_ladder_gates(self):
        for change in (
            {"scan_reason": "private"}, {"metadata": {}},
            {"excerpt": "Different public example"},
        ):
            with self.subTest(change=change):
                self.store()
                self.assertIsNone(self.consume(capture={**self.capture, **change}))
        self.store()
        self.controller.public_or_sanitized_data_ack = False
        self.assertIsNone(self.consume())
        self.controller.public_or_sanitized_data_ack = True
        self.assertIsNone(self.consume(candidates=["low", "high"]))
        self.assertIsNone(self.consume(cap="low", candidates=["low"]))
        self.assertEqual(len(self.broker.receipts), 0)

    def test_medium_cap_uses_only_its_own_ladder(self):
        self.store()
        actual = self.consume(cap="medium", candidates=["low", "medium"])
        assert actual is not None
        self.assertEqual(actual["effort"], "low")
        self.assertEqual(self.broker.receipts[0]["cap"], "medium")

    def test_missing_request_id_or_multiple_requests_are_not_stored(self):
        for change in ({"request_id": None}, {"request_id": " "}, {"request_count": 2}):
            with self.subTest(change=change):
                self.store(response={**self.response, **change})
                self.assertIsNone(self.consume())
        self.assertEqual(len(self.broker.receipts), 0)

    def test_deadline_and_expiry_discard_records(self):
        self.store(elapsed_ms=401)
        self.assertIsNone(self.consume())
        with mock.patch("hermes_switchyard.combined_decisions.time.monotonic", return_value=10):
            self.store()
        with mock.patch("hermes_switchyard.combined_decisions.time.monotonic", return_value=41):
            self.assertIsNone(self.consume())
        self.assertEqual(len(self.broker.receipts), 0)

    def test_clear_and_bounded_pending_cache(self):
        self.store()
        self.broker.clear(self.KEY)
        self.assertIsNone(self.consume())
        for number in range(33):
            self.store(("public-session", "public-task", str(number)))
        self.assertEqual(len(self.broker.records), 32)
        self.assertNotIn(("public-session", "public-task", "0"), self.broker.records)

    def test_response_is_copied_before_caller_can_mutate_it(self):
        self.store()
        self.response["answers"].clear()
        self.assertIsNotNone(self.consume())

    def test_changed_or_unavailable_identity_rejects_result_at_store(self):
        identity = self.broker._route_identity()
        for replacement in ({"route": "different"}, None):
            with self.subTest(identity=replacement):
                self.identity = replacement
                self.broker.store(
                    self.KEY, self.TASK, self.response, 1, request_identity=identity,
                    generation=self.broker.begin(self.KEY),
                )
                self.assertIsNone(self.consume())
        self.assertEqual(len(self.broker.receipts), 0)

    def test_superseded_and_cancelled_producers_cannot_store_late_results(self):
        old = self.broker.begin(self.KEY)
        latest = self.broker.begin(self.KEY)
        self.broker.store(self.KEY, self.TASK, self.response, 1,
                          request_identity=self.broker._route_identity(), generation=old)
        self.assertIsNone(self.consume())
        self.broker.invalidate()
        self.broker.store(self.KEY, self.TASK, self.response, 1,
                          request_identity=self.broker._route_identity(), generation=latest)
        self.assertIsNone(self.consume())
        self.assertEqual(len(self.broker.receipts), 0)

    def test_cancellation_during_replay_cannot_publish_shared_receipt(self):
        from hermes_switchyard import combined_decisions
        original = combined_decisions.choose_reasoning_effort
        self.store()

        def cancelled(**kwargs):
            selected = original(**kwargs)
            self.broker.invalidate()
            return selected

        with mock.patch.object(combined_decisions, "choose_reasoning_effort", side_effect=cancelled):
            self.assertIsNone(self.consume())
        self.assertEqual(len(self.broker.receipts), 0)

    def test_invalid_identity_factory_fails_closed(self):
        for factory in (
            lambda: None, lambda: {"invalid": float("nan")},
            lambda: {"invalid": object()}, mock.Mock(side_effect=RuntimeError("fixture")),
        ):
            with self.subTest(factory=factory):
                self.broker.identity_factory = factory
                self.assertIsNone(self.broker._route_identity())
        self.assertEqual(len(self.broker.receipts), 0)


if __name__ == "__main__":
    unittest.main()
