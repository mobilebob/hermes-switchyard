"""Offline, credential-free durable-collector contract tests."""
from __future__ import annotations

import importlib.util
from contextlib import closing
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "evaluation/jev_skills_comparison"
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("stage_a2_collector", HERE / "stage_a2_collector.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.path = Path(self.t.name)
        self.root = self.path / "study"
        self.auth = self.path / "auth"
        self.hermes = self.path / "runtime"
        self.auth.mkdir()
        self.hermes.mkdir()
        self.patch_identity = mock.patch.object(collector, "git_identity", return_value="a" * 40)
        self.patch_identity.start()
        self.addCleanup(self.patch_identity.stop)
        self.tasks, self.plan_sha = collector.require_plan()

    @staticmethod
    def fixture(intent):
        model = intent["model_id"].split(":", 1)[1]
        return {"attempt_id": intent["attempt_id"], "provider": "openai-codex",
                "requested_model": model, "wire_model": model, "effective_model": model,
                "physical_call_count": 1, "fallback_enabled": False, "tools_enabled": False,
                "completed": True, "answer": collector.require_plan()[0][intent["case_id"]]["expected"],
                "output_policy": collector.OUTPUT_POLICY, "output_token_acceptance_max": collector.OUTPUT_LIMIT,
                "provider_output_cap_enforced": False,
                "input_tokens": 95, "output_tokens": 9,
                "runtime_modules_sha256": "f" * 64}

    def test_plan_and_seed_are_stable(self):
        self.assertEqual(self.plan_sha, collector.FROZEN_PLAN_SHA)
        pairs = collector.ordered_pairs(self.tasks)
        self.assertEqual(len(pairs), 48)
        self.assertEqual(len(set(pairs)), 48)
        self.assertEqual(pairs, collector.ordered_pairs(self.tasks))

    def test_preparation_is_exclusive_and_creates_no_request(self):
        collector.prepare(self.root)
        with closing(sqlite3.connect(self.root / "attempts.sqlite3")) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM attempts").fetchone()[0], 0)
        with self.assertRaisesRegex(collector.StudyError, "already exists"):
            collector.prepare(self.root)
        self.assertIsNone(collector.load_meta(self.root)["monetary_cap"])
        self.assertIsNone(collector.load_meta(self.root)["output_token_cap"])
        self.assertEqual(collector.load_meta(self.root)["output_policy"], collector.OUTPUT_POLICY)

    def test_47_fixture_calls_exclude_prior_uncertain_pair(self):
        collector.prepare(self.root)
        actual = []
        def runner(intent):
            actual.append((intent["case_id"], intent["model_id"]))
            return self.fixture(intent)
        report = collector.execute(self.root, self.auth, self.hermes, max_new=48, runner=runner)
        self.assertEqual(len(actual), 47)
        self.assertEqual(len(set(actual)), 47)
        self.assertNotIn(("extract-c", "openai-codex:gpt-6.1-sol"), actual)
        self.assertEqual(report["status"], "incomplete_or_unresolved")
        self.assertEqual(report["paired_outcomes"]["both_correct"], 23)
        self.assertEqual(report["paired_outcomes"]["not_scored"], 1)
        self.assertFalse(report["provider_output_cap_enforced"])
        self.assertFalse(report["provider_calls_independently_verified"])
        self.assertFalse(report["promotion_authorized"])
        again = collector.execute(self.root, self.auth, self.hermes, max_new=48, runner=runner)
        self.assertEqual(len(actual), 47)
        self.assertEqual(again["collector_intents_persisted"], 47)

    def test_output_threshold_exceeded_preserves_usage_and_stops(self):
        collector.prepare(self.root)
        requested = []
        def oversized(intent):
            requested.append((intent["case_id"], intent["model_id"]))
            return {**self.fixture(intent), "output_tokens": 129}
        result = collector.execute(self.root, self.auth, self.hermes, max_new=48, runner=oversized)
        self.assertEqual(len(requested), 1)
        self.assertNotIn(("extract-c", "openai-codex:gpt-6.1-sol"), requested)
        self.assertEqual(result["posthoc_output_threshold_exceeded"], 1)
        row = json.loads((self.root / "observations.jsonl").read_text().splitlines()[0])
        self.assertEqual(row["status"], "failed")
        self.assertIsNone(row["answer"])
        self.assertEqual((row["input_tokens"], row["output_tokens"]), (95, 129))
        self.assertTrue(row["collector_receipt"]["posthoc_output_threshold_exceeded"])
        with self.assertRaisesRegex(collector.StudyError, "Unresolved prior intent"):
            collector.execute(self.root, self.auth, self.hermes, max_new=1, runner=oversized)
        self.assertEqual(len(requested), 1)

    def test_missing_response_policy_is_uncertain(self):
        collector.prepare(self.root)
        def incomplete(intent):
            result = self.fixture(intent)
            result.pop("output_policy")
            return result
        collector.execute(self.root, self.auth, self.hermes, max_new=1, runner=incomplete)
        row = json.loads((self.root / "observations.jsonl").read_text().splitlines()[0])
        self.assertEqual(row["status"], "uncertain")

    def test_resume_continues_unattempted_pairs_but_not_previous_ones(self):
        collector.prepare(self.root)
        done = []
        def runner(intent):
            done.append(intent["attempt_id"])
            return self.fixture(intent)
        first = collector.execute(self.root, self.auth, self.hermes, max_new=3, runner=runner)
        self.assertEqual(first["collector_intents_persisted"], 3)
        second = collector.execute(self.root, self.auth, self.hermes, max_new=4, runner=runner)
        self.assertEqual(second["collector_intents_persisted"], 7)
        self.assertEqual(len(set(done)), 7)

    def test_uncertain_response_blocks_entire_future_dispatch(self):
        collector.prepare(self.root)
        calls = []
        def runner(intent):
            calls.append(intent["attempt_id"])
            return {**self.fixture(intent), "physical_call_count": 2}
        out = collector.execute(self.root, self.auth, self.hermes, max_new=48, runner=runner)
        self.assertEqual(out["models"][collector.APPROVED_MODELS[0]]["uncertain"] +
                         out["models"][collector.APPROVED_MODELS[1]]["uncertain"], 1)
        with self.assertRaisesRegex(collector.StudyError, "Unresolved prior intent"):
            collector.execute(self.root, self.auth, self.hermes, max_new=48, runner=runner)
        self.assertEqual(len(calls), 1)

    def test_killed_worker_keeps_durable_intent_and_never_retries(self):
        collector.prepare(self.root)
        called = []
        def crash(intent):
            called.append(intent["attempt_id"])
            raise SystemExit("interrupted after reservation")
        with self.assertRaises(SystemExit):
            collector.execute(self.root, self.auth, self.hermes, max_new=1, runner=crash)
        with closing(sqlite3.connect(self.root / "attempts.sqlite3")) as db:
            status = db.execute("SELECT status FROM attempts").fetchone()[0]
        self.assertEqual(status, "intent")
        with self.assertRaisesRegex(collector.StudyError, "Unresolved prior intent"):
            collector.execute(self.root, self.auth, self.hermes, max_new=1, runner=crash)
        self.assertEqual(len(called), 1)

    def test_plan_or_collector_drift_blocks_dispatch(self):
        collector.prepare(self.root)
        manifest = json.loads((self.root / "manifest.json").read_text())
        manifest["source_sha256"]["stage_a2_collector.py"] = "0" * 64
        collector.atomic_json(self.root / "manifest.json", manifest)
        with self.assertRaisesRegex(collector.StudyError, "manifest diverged"):
            collector.execute(self.root, self.auth, self.hermes, max_new=1, runner=self.fixture)
        with closing(sqlite3.connect(self.root / "attempts.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0], 0)

    def test_cli_execute_requires_explicit_authorization(self):
        collector.prepare(self.root)
        rc = collector.main(["--execute", "--output", str(self.root),
                             "--auth-home", str(self.auth), "--hermes-root", str(self.hermes)])
        self.assertEqual(rc, 2)
        with closing(sqlite3.connect(self.root / "attempts.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0], 0)

    def test_native_runtime_bridge_refuses_unknown_entrypoint(self):
        with self.assertRaisesRegex(collector.StudyError, "native worker seam"):
            collector.worker_command(["python", "-c", "print('not Hermes')"])

    def test_wrong_model_or_tool_receipt_is_uncertain(self):
        collector.prepare(self.root)
        def bad(intent):
            return {**self.fixture(intent), "effective_model": "other:unapproved"}
        result = collector.execute(self.root, self.auth, self.hermes, max_new=3, runner=bad)
        self.assertEqual(result["collector_intents_persisted"], 1)
        self.assertEqual(result["rows_received"], 1)
        self.assertEqual(result["status"], "incomplete_or_unresolved")


if __name__ == "__main__":
    unittest.main()
