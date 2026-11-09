"""Offline, isolated tests for the optional Jev usage ledger and its client hook."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from hermes_switchyard import usage_ledger
from hermes_switchyard.client import DecisionClient, TYPESAFE_ENDPOINT

NOW = datetime(2026, 11, 8, 14, 0, tzinfo=timezone.utc)


class UsageLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="jev-usage-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_validated_cost_and_tokens_are_observed_not_billed(self):
        attempt = usage_ledger.begin("typesafe", data_dir=self.root, now=NOW, force=True)
        self.assertRegex(attempt, r"^[0-9a-f]{32}$")
        self.assertTrue(usage_ledger.finish(
            attempt, "typesafe", outcome="validated",
            usage={"cost": 0.0012, "total_tokens": 123.0},
            retries={"stale_connection": 1}, data_dir=self.root, now=NOW
        ))
        result = usage_ledger.report(days=7, data_dir=self.root, now=NOW)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["begin_count"], 1)
        self.assertEqual(result["validated"], 1)
        self.assertEqual(result["known_cost_usd"], 0.0012)
        self.assertEqual(result["observed_total_tokens"], 123)
        self.assertEqual(result["transport_retries"], 1)
        self.assertFalse(result["billing_verified"])
        raw = (self.root / usage_ledger.FILENAME).read_text()
        self.assertNotIn("test-secret", raw)
        self.assertNotIn(str(self.root), raw)

    def test_pending_failure_and_missing_cost_stay_unknown(self):
        pending = usage_ledger.begin("openrouter", data_dir=self.root, now=NOW, force=True)
        failed = usage_ledger.begin("openrouter", data_dir=self.root, now=NOW, force=True)
        missing = usage_ledger.begin("typesafe", data_dir=self.root, now=NOW, force=True)
        self.assertTrue(pending)
        self.assertTrue(usage_ledger.finish(
            failed, "openrouter", outcome="unresolved",
            usage={"cost": 999.0, "total_tokens": 999},
            data_dir=self.root, now=NOW,
        ))
        self.assertTrue(usage_ledger.finish(
            missing, "typesafe", outcome="validated",
            usage={"total_tokens": 30}, data_dir=self.root, now=NOW
        ))
        result = usage_ledger.report(data_dir=self.root, now=NOW)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["pending"], 1)
        self.assertEqual(result["unresolved"], 1)
        self.assertEqual(result["validated"], 1)
        self.assertEqual(result["unknown_cost_requests"], 3)
        self.assertIsNone(result["known_cost_usd"])
        self.assertEqual(result["observed_total_tokens"], 30)

    def test_window_does_not_count_old_attempt(self):
        old = NOW - timedelta(days=10)
        attempt = usage_ledger.begin("typesafe", data_dir=self.root, now=old, force=True)
        usage_ledger.finish(attempt, "typesafe", outcome="validated",
                            usage={"cost": 0.1}, data_dir=self.root, now=old)
        self.assertEqual(usage_ledger.report(days=7, data_dir=self.root, now=NOW)["begin_count"], 0)
        self.assertEqual(usage_ledger.report(days=30, data_dir=self.root, now=NOW)["begin_count"], 1)

    def test_malformed_records_never_hide_unknowns_or_text(self):
        attempt = usage_ledger.begin("typesafe", data_dir=self.root, now=NOW, force=True)
        with (self.root / usage_ledger.FILENAME).open("ab") as handle:
            handle.write(b'{"private":"DO_NOT_ECHO"}\n')
        report = usage_ledger.report(data_dir=self.root, now=NOW)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["invalid_lines"], 1)
        self.assertEqual(report["pending"], 1)
        self.assertNotIn("DO_NOT_ECHO", json.dumps(report))

    def test_symlink_rejected(self):
        outside = self.root / "outside"
        outside.write_text("private")
        link_root = self.root / "linked"
        link_root.mkdir()
        (link_root / usage_ledger.FILENAME).symlink_to(outside)
        self.assertIsNone(usage_ledger.begin("typesafe", data_dir=link_root, force=True))
        self.assertEqual(usage_ledger.report(data_dir=link_root)["reason"], "ledger_unreadable")
        self.assertEqual(outside.read_text(), "private")

    def test_cost_validation_rejects_overlarge_numbers(self):
        self.assertFalse(usage_ledger._finite_cost(10**400))
        self.assertFalse(usage_ledger._finite_cost(float("nan")))
        self.assertFalse(usage_ledger._finite_cost(-1))

    def test_default_off_and_invalid_window(self):
        with mock.patch.dict(os.environ, {"SWITCHYARD_JEV_USAGE_LEDGER": "off"}):
            self.assertFalse(usage_ledger.enabled())
            self.assertIsNone(usage_ledger.begin("typesafe", data_dir=self.root))
        with self.assertRaises(ValueError):
            usage_ledger.report(days=0, data_dir=self.root)
        with self.assertRaises(ValueError):
            usage_ledger.report(days=3651, data_dir=self.root)

    def test_client_records_only_validated_real_transport_result(self):
        client = DecisionClient(api_key="fixture-key", endpoint=TYPESAFE_ENDPOINT, model="jev-latest")
        response = {"usage": {"cost": 0.0002, "total_tokens": 13}, "transport_retries": {"http_429": 1}}
        with mock.patch.object(usage_ledger, "enabled", return_value=True), \
             mock.patch.object(usage_ledger, "begin", return_value="a" * 32) as begin, \
             mock.patch.object(usage_ledger, "finish", return_value=True) as finish, \
             mock.patch.object(client, "_decide_single_unmetered", return_value=response):
            self.assertIs(client._decide_single({"task": "public"}, {"a": {}}), response)
        begin.assert_called_once_with("typesafe")
        self.assertEqual(finish.call_args.kwargs["outcome"], "validated")
        self.assertEqual(finish.call_args.kwargs["usage"], response["usage"])

    def test_client_failure_is_unknown_and_unchanged(self):
        client = DecisionClient(api_key="fixture-key", endpoint=TYPESAFE_ENDPOINT, model="jev-latest")
        with mock.patch.object(usage_ledger, "enabled", return_value=True), \
             mock.patch.object(usage_ledger, "begin", return_value="b" * 32), \
             mock.patch.object(usage_ledger, "finish", return_value=True) as finish, \
             mock.patch.object(client, "_decide_single_unmetered", side_effect=TimeoutError("late")):
            with self.assertRaises(TimeoutError):
                client._decide_single({"task": "public"}, {"a": {}})
        self.assertEqual(finish.call_args.kwargs["outcome"], "unresolved")

    def test_synthetic_transport_never_meters(self):
        client = DecisionClient(api_key="fixture-key", endpoint=TYPESAFE_ENDPOINT,
                                model="jev-latest", transport=lambda _: {})
        with mock.patch.object(usage_ledger, "enabled", return_value=True), \
             mock.patch.object(usage_ledger, "begin") as begin, \
             mock.patch.object(client, "_decide_single_unmetered", return_value={"usage": {}}):
            client._decide_single({"task": "public"}, {"a": {}})
        begin.assert_not_called()


if __name__ == "__main__":
    unittest.main()
