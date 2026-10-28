"""Offline, synthetic tests for read-only duplicate skill_view observation."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_switchyard import receipt_history
from hermes_switchyard.skill_duplicates import build_report, format_text

NOW = datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc)
A = "a" * 40
B = "b" * 40


def history(stamp, session="s1", skill="docker-management", sha=A):
    return {
        "recorded_at": receipt_history.format_timestamp(stamp),
        "session_id": session,
        "receipt": {
            "consumer_status": "loaded" if skill else "abstained",
            "skill_load_verified": bool(skill),
            "loaded_skill": skill,
            "source_sha": sha,
        },
    }


def payload(name="docker-management", *, stub=False, file=None, sensitive=None):
    if stub:
        return json.dumps({
            "success": True, "name": name, "file": "SKILL.md",
            "status": "unchanged", "dedup": True, "content_returned": False,
        })
    result = {
        "success": True, "name": name, "content": sensitive or "Synthetic instructions"
    }
    if file:
        result["file"] = file
    return json.dumps(result)


class DuplicateAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="switchyard-duplicate-test-")
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "state.db"
        self.conn = sqlite3.connect(self.db)
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY);
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT,
                tool_name TEXT,
                timestamp REAL,
                content TEXT,
                active INTEGER DEFAULT 1
            );
            INSERT INTO sessions (id) VALUES ('s1'), ('s2');
        """)
        self.base = NOW - timedelta(hours=2)

    def add(self, session, seconds, content, *, active=1):
        self.conn.execute(
            "INSERT INTO messages(session_id, role, tool_name, timestamp, content, active) "
            "VALUES (?, 'tool', 'skill_view', ?, ?, ?)",
            (session, (self.base + timedelta(seconds=seconds)).timestamp(), content, active),
        )
        self.conn.commit()

    def report(self, records):
        return build_report(days=7, now=NOW, db_path=self.db, records=records)

    def test_full_reread_stub_and_different_skill_are_distinguished(self):
        full = payload(sensitive="SYNTHETIC_PRIVATE_PAYLOAD")
        self.add("s1", 10, full)
        self.add("s1", 20, payload("other-skill"))
        self.add("s1", 205, payload(stub=True))
        receipts = [
            history(self.base, "s1", sha=A),
            history(self.base + timedelta(seconds=180), "s1", sha=B),
            history(self.base + timedelta(seconds=220), "s2", sha=B),
        ]
        result = self.report(receipts)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["coverage"]["eligible"], 3)
        self.assertEqual(result["metrics"]["full_refetch_turns"], 1)
        self.assertEqual(result["metrics"]["full_refetch_calls"], 1)
        self.assertEqual(result["metrics"]["unchanged_stub_turns"], 1)
        self.assertEqual(result["metrics"]["different_skill_turns"], 1)
        self.assertEqual(result["metrics"]["full_refetch_rate_pct"], 33.3)
        self.assertEqual(result["metrics"]["redundant_result_chars"], len(full))
        self.assertEqual(result["by_revision"][A]["full_refetch_turns"], 1)
        self.assertEqual(result["by_revision"][B]["eligible"], 2)
        self.assertNotIn("SYNTHETIC_PRIVATE_PAYLOAD", json.dumps(result))
        self.assertNotIn(str(self.db), format_text(result))
        self.assertNotIn("docker-management", format_text(result))

    def test_next_receipt_bounds_the_attribution_window(self):
        self.add("s1", 30, payload())
        receipts = [
            history(self.base, "s1"),
            history(self.base + timedelta(seconds=20), "s1", skill=None),
        ]
        result = self.report(receipts)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["metrics"]["full_refetch_turns"], 0)
        self.assertEqual(result["coverage"]["eligible"], 1)

    def test_linked_file_is_not_the_preloaded_skill_body(self):
        self.add("s1", 10, payload(file="references/readme.md"))
        result = self.report([history(self.base)])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["metrics"]["full_refetch_turns"], 0)

    def test_unknown_result_marks_partial_not_no_duplicate(self):
        self.add("s1", 5, "{malformed")
        receipts = [
            history(self.base, "s1"),
            history(self.base + timedelta(seconds=200), "s2"),
        ]
        result = self.report(receipts)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["eligible"], 1)
        self.assertEqual(result["coverage"]["ambiguous"], 1)
        self.assertEqual(result["metrics"]["full_refetch_rate_pct"], 0.0)

    def test_missing_session_is_partial(self):
        result = self.report([history(self.base, session="s1"),
                              history(self.base, session="nonexistent")])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["unmatched_sessions"], 1)
        self.assertEqual(result["coverage"]["eligible"], 1)

    def test_inactive_result_is_not_counted(self):
        self.add("s1", 10, payload(), active=0)
        result = self.report([history(self.base)])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["metrics"]["full_refetch_turns"], 0)

    def test_pending_window_is_not_counted_as_a_zero(self):
        result = self.report([history(NOW - timedelta(seconds=30))])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "no_attributable_windows")
        self.assertEqual(result["coverage"]["pending"], 1)

    def test_unavailable_db_is_not_an_empty_transcript(self):
        report = build_report(
            now=NOW, db_path=Path(self.tmp.name) / "missing.db",
            records=[history(self.base)]
        )
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "state_db_unavailable")
        self.assertEqual(report["metrics"]["full_refetch_rate_pct"], None)

    def test_no_preloads_and_invalid_window(self):
        self.assertEqual(self.report([history(self.base, skill=None)])["reason"],
                         "no_retained_preloads")
        with self.assertRaises(ValueError):
            build_report(days=0, now=NOW, db_path=self.db, records=[])


    def test_cli_command_uses_report_without_opening_provider(self):
        import argparse
        import contextlib
        import io
        from unittest import mock
        import hermes_switchyard
        from hermes_switchyard import skill_duplicates

        parser = argparse.ArgumentParser()
        hermes_switchyard._setup_cli(parser)
        args = parser.parse_args(["skill-duplicates", "--days", "7", "--json"])
        expected = self.report([history(self.base)])
        out = io.StringIO()
        with mock.patch.object(skill_duplicates, "build_report", return_value=expected) as mocked:
            with contextlib.redirect_stdout(out):
                rc = hermes_switchyard._cli_handler(args)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue()), expected)
        mocked.assert_called_once_with(days=7)

        invalid = parser.parse_args(["skill-duplicates", "--days", "0"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(hermes_switchyard._cli_handler(invalid), 2)


if __name__ == "__main__":
    unittest.main()
