"""Offline evidence-bound tests for runtime provenance."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from hermes_switchyard import receipt_history, runtime_provenance

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
OLD = "a" * 40
NEW = "b" * 40
STARTED = NOW - timedelta(minutes=10)


def record(sha: str, *, minute: int, platform: str | None = "whatsapp"):
    return {
        "recorded_at": receipt_history.format_timestamp(STARTED + timedelta(minutes=minute)),
        "platform": platform, "receipt": {"source_sha": sha},
    }


def svc():
    return {"status": "running", "pid": 1234, "started_at": receipt_history.format_timestamp(STARTED)}


class RuntimeProvenanceTests(unittest.TestCase):
    def test_recent_gateway_receipt_is_observed_but_not_attested(self):
        with mock.patch.object(receipt_history, "resolve_git_source_sha", return_value=NEW), \
             mock.patch.object(receipt_history, "resolve_receipt_source_sha", return_value=NEW):
            result = runtime_provenance.report(
                now=NOW, records=[
                    record(OLD, minute=-2),
                    record(NEW, minute=2, platform="cli"),
                    record(NEW, minute=3, platform="whatsapp")
                ], service=svc())
        self.assertEqual(result["status"], "runtime_receipt_observed")
        self.assertEqual(result["matching_post_start_receipts"], 1)
        self.assertEqual(result["receipts_by_revision"], {OLD: 1, NEW: 2})
        self.assertEqual(result["gateway_like_receipts_since_service_start_by_revision"], {NEW: 1})
        self.assertFalse(result["gateway_runtime_verified"])
        self.assertNotIn("session", json.dumps(result).lower().replace("profile-specific history may be incomplete", ""))

    def test_checkout_and_manifest_mismatch_is_not_verified(self):
        with mock.patch.object(receipt_history, "resolve_git_source_sha", return_value=NEW), \
             mock.patch.object(receipt_history, "resolve_receipt_source_sha", return_value=OLD):
            result = runtime_provenance.report(now=NOW, records=[
                record(NEW, minute=3)
            ], service=svc())
        self.assertEqual(result["status"], "unverified")
        self.assertFalse(result["installed_sources_consistent"])
        self.assertFalse(result["gateway_runtime_verified"])

    def test_no_post_start_gateway_receipt_is_unverified(self):
        with mock.patch.object(receipt_history, "resolve_git_source_sha", return_value=NEW), \
             mock.patch.object(receipt_history, "resolve_receipt_source_sha", return_value=NEW):
            result = runtime_provenance.report(now=NOW, records=[
                record(NEW, minute=-2), record(NEW, minute=2, platform="cli")
            ], service=svc())
        self.assertEqual(result["matching_post_start_receipts"], 0)
        self.assertEqual(result["status"], "unverified")

    def test_service_unavailable_is_not_positive_proof(self):
        with mock.patch.object(receipt_history, "resolve_git_source_sha", return_value=NEW), \
             mock.patch.object(receipt_history, "resolve_receipt_source_sha", return_value=NEW):
            result = runtime_provenance.report(now=NOW, records=[
                record(NEW, minute=3)
            ], service={"status": "unavailable"})
        self.assertEqual(result["status"], "unverified")
        self.assertFalse(result["gateway_runtime_verified"])

    def test_systemd_probe_only_uses_bounded_read_only_fields(self):
        stdout = "MainPID=1234\nActiveEnterTimestamp=Sun 2026-11-08 11:50:00 UTC\n"
        with mock.patch.object(runtime_provenance.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = stdout
            result = runtime_provenance._gateway_service()
        self.assertEqual(result["pid"], 1234)
        self.assertEqual(result["started_at"], "2026-11-08T11:50:00Z")
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs["timeout"], 2.0)

    def test_invalid_window(self):
        with self.assertRaises(ValueError):
            runtime_provenance.report(days=0, now=NOW, records=[], service=svc())


if __name__ == "__main__":
    unittest.main()
