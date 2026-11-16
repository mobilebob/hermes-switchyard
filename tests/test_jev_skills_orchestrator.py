"""Orchestrator tests use a labeled stub worker, not claimed routing evidence."""
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1] / "evaluation/jev_skills_comparison"
spec = importlib.util.spec_from_file_location("shadow_orchestrator", HERE / "collect.py")
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class OrchestratorTests(unittest.TestCase):
    def worker(self, args, **kwargs):
        payload = json.loads(kwargs["input"])
        self.homes.append(kwargs["cwd"])
        self.assertEqual(kwargs["env"]["HOME"], str(kwargs["cwd"]))
        self.assertIn("-I", args)
        self.assertEqual(payload["case"]["task"], mod.read_cases(HERE / "cases.json")[payload["case"]["id"]]["task"])
        row = {"proposed_model": None, "abstained": True, "abstention_reason": "stub_unavailable",
               "implementation_status": "abstained", "provider_requests": [], "simulated_requests": 1,
               "task_received": payload["case"]["task"], "candidate_ids": ["fixture:small", "fixture:large"],
               "network_attempts": 0, "process_attempts": 0, "loaded_source_root": payload["source_root"],
               "worker_failed": False}
        row.update(self.updates)
        return types.SimpleNamespace(stdout=json.dumps(row))

    def setUp(self):
        self.homes = []
        self.updates = {}
        self.roots = {a: Path(a) for a in mod.ARMS}

    def collect(self, out):
        with patch.object(mod, "verify_source", side_effect=lambda r, a: {"source_sha": mod.PINS[a]}), \
             patch.object(mod.subprocess, "run", side_effect=self.worker):
            return mod.collect(self.roots, out, "no-response", HERE / "catalog.json")

    def test_exact_cartesian_coverage_separate_homes_and_persisted_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            report = self.collect(out)
            rows = [json.loads(line) for line in (out / "observations.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), 24)
            self.assertEqual(len({(r["case_id"], r["arm"]) for r in rows}), 24)
            self.assertEqual(len(set(self.homes)), 24)
            self.assertTrue(all(not p.exists() for p in self.homes))
            self.assertEqual(json.loads((out / "comparison.json").read_text()), report)
            self.assertTrue(all(r["applied"] is False for r in rows))
            self.assertEqual(report["status"], "offline_no_response_only")
            with self.assertRaises(FileExistsError):
                self.collect(out)

    def test_input_mismatch_and_attempted_egress_fail(self):
        for updates in ({"task_received": "different"}, {"candidate_ids": []},
                        {"provider_requests": [{}]}, {"network_attempts": 1}, {"process_attempts": 1},
                        {"loaded_source_root": "elsewhere"}):
            with self.subTest(updates=updates), tempfile.TemporaryDirectory() as tmp:
                self.updates = updates
                with self.assertRaises(ValueError):
                    self.collect(Path(tmp) / "run")

    def test_worker_timeout_is_failure_not_successful_abstention(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            with patch.object(mod, "verify_source", side_effect=lambda r, a: {"source_sha": mod.PINS[a]}), \
                 patch.object(mod.subprocess, "run", side_effect=mod.subprocess.TimeoutExpired("worker", 20)):
                report = mod.collect(self.roots, out, "no-response", HERE / "catalog.json")
            self.assertEqual(report["status"], "incomplete_or_blocked")
            self.assertEqual(len(report["arms"]["switchyard"]["worker_failures"]), 12)


if __name__ == "__main__":
    unittest.main()
