"""Offline P1 guard tests for the existing isolated Jev consolidation pilot.

No provider calls, gateway startup, credential reads, or plugin registration.
These tests do NOT qualify the prototype for production.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "evaluation/turn_consolidation/pilot.py"
spec = importlib.util.spec_from_file_location(
    "hermes_switchyard._integration_broker_gate", SOURCE
)
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


class CombinedDecisionBrokerGates(unittest.TestCase):
    KEY = ("session-public-1", "task-public-1", "turn-public-1")
    OTHER = ("session-public-2", "task-public-2", "turn-public-2")
    TASK = "Use the synthetic public skill catalog to describe a CSV."

    def setUp(self):
        self.broker = pilot.Broker()
        self.controller = SimpleNamespace(
            public_or_sanitized_data_ack=True, deadline_seconds=2
        )
        self.capture = {
            "excerpt": self.TASK, "scan_reason": None, "metadata": None
        }

    def store(self, key=None, *, request_id="public-jev-request-1",
              age_seconds=0, latency_ms=20):
        key = self.KEY if key is None else key
        self.broker.store(key, {
            "task": self.TASK,
            "result": {
                "request_id": request_id,
                "answers": {
                    "reasoning_effort_high": {"value": "low"},
                    "reasoning_effort_medium": {"value": "low"},
                },
            },
            "at": time.monotonic() - age_seconds,
            "wall_ms": latency_ms,
        })

    def consume(self, key=None, *, capture=None, effort="high",
                candidates=None, controller=None):
        key = self.KEY if key is None else key
        capture = self.capture if capture is None else capture
        candidates = (["low", "medium", "high"]
                      if candidates is None else candidates)
        controller = self.controller if controller is None else controller
        with mock.patch.object(
            pilot, "choose_reasoning_effort",
            return_value={"status": "selected", "effort": "low"},
        ):
            return self.broker.consume(
                key, capture, effort, candidates, controller
            )

    def test_one_request_one_consumer_one_receipt(self):
        self.store()
        first = self.consume()
        self.assertIsNotNone(first)
        self.assertIs(first["jev_called"], False)
        self.assertIs(first["shared_decision"], True)
        self.assertEqual(first["effort"], "low")
        self.assertEqual(len(self.broker.receipts), 1)
        self.assertEqual(self.broker.receipts[0]["shared_request_id"],
                         "public-jev-request-1")
        self.assertIsNone(self.consume())
        self.assertEqual(len(self.broker.receipts), 1)

    def test_other_session_cannot_consume_this_turn(self):
        self.store()
        self.assertIsNone(self.consume(self.OTHER))
        self.assertIsNotNone(self.consume())
        self.assertEqual(len(self.broker.receipts), 1)

    def test_mismatched_task_not_shared(self):
        self.store()
        bad = {**self.capture, "excerpt": "Different public request"}
        self.assertIsNone(self.consume(capture=bad))
        self.assertEqual(self.broker.receipts, [])

    def test_privacy_scan_or_metadata_declines(self):
        for change in ({"scan_reason": "private"}, {"metadata": {"secret": True}}):
            with self.subTest(change=change):
                self.broker = pilot.Broker()
                self.store()
                self.assertIsNone(self.consume(capture={**self.capture, **change}))
                self.assertEqual(self.broker.receipts, [])

    def test_consent_required(self):
        self.store()
        blocked = SimpleNamespace(
            public_or_sanitized_data_ack=False, deadline_seconds=2
        )
        self.assertIsNone(self.consume(controller=blocked))
        self.assertEqual(self.broker.receipts, [])

    def test_effort_ladder_must_match_cap(self):
        self.store()
        self.assertIsNone(self.consume(
            effort="high", candidates=["low", "high"]
        ))
        self.assertIsNone(self.consume(effort="low"))
        self.assertEqual(self.broker.receipts, [])

    def test_missing_provider_request_id_not_reused(self):
        for rid in (None, "", " "):
            with self.subTest(id=rid):
                self.broker = pilot.Broker()
                self.store(request_id=rid)
                self.assertIsNone(self.consume())
                self.assertIsNone(self.consume())
                self.assertEqual(self.broker.receipts, [])

    def test_expired_result_not_reused(self):
        self.store(age_seconds=31)
        self.assertIsNone(self.consume())
        self.assertEqual(self.broker.receipts, [])

    def test_slow_request_not_reused(self):
        self.store(latency_ms=2001)
        self.assertIsNone(self.consume())
        self.assertEqual(self.broker.receipts, [])

    def test_clear_discards_result_after_cancellation(self):
        self.store()
        self.broker.clear(self.KEY)
        self.assertIsNone(self.consume())
        self.assertEqual(self.broker.receipts, [])

    def test_bounded_cache_evicts_oldest(self):
        for i in range(33):
            self.broker.store(("s", "t", str(i)), {
                "task": self.TASK, "result": {"request_id": str(i)},
                "at": time.monotonic(), "wall_ms": 20,
            })
        self.assertEqual(len(self.broker.records), 32)
        self.assertNotIn(("s", "t", "0"), self.broker.records)
        self.assertIn(("s", "t", "32"), self.broker.records)

    def test_concurrent_consumers_cannot_duplicate_receipt(self):
        self.store()
        with mock.patch.object(
            pilot, "choose_reasoning_effort",
            return_value={"status": "selected", "effort": "low"},
        ):
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [
                    pool.submit(
                        self.broker.consume, self.KEY, self.capture, "high",
                        ["low", "medium", "high"], self.controller
                    ) for _ in range(8)
                ]
                responses = [future.result() for future in futures]
        self.assertEqual(sum(r is not None for r in responses), 1)
        self.assertEqual(len(self.broker.receipts), 1)


if __name__ == "__main__":
    unittest.main()
