"""Offline evidence and grading contracts; no provider/model invocation."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "evaluation/jev_skills_comparison"
spec = importlib.util.spec_from_file_location("correctness_evaluator", SRC / "correctness.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
TASKS, SHA = mod.load_plan(SRC / "heldout_cases.json")


def row(case_id, model_id, **overrides):
    expected = TASKS[case_id]["expected"]
    record = {
        "case_id": case_id, "model_id": model_id,
        "plan_sha256": SHA,
        "prompt_sha256": mod.digest(TASKS[case_id]["prompt"].encode("utf-8")),
        "attempt_id": case_id + ":" + model_id,
        "evidence_mode": "fixture", "routing_applied": False, "status": "completed",
        "answer": expected, "elapsed_ms": 123, "input_tokens": 80, "output_tokens": 5,
    }
    return {**record, **overrides}


def all_rows():
    return {(id_, model): row(id_, model) for id_ in TASKS for model in mod.APPROVED_MODELS}


class CorrectnessTests(unittest.TestCase):
    def read_lines(self, rows):
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "results.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            return mod.read_observations(path, TASKS, SHA)

    def test_plan_is_paired_and_has_code_trace_contrast(self):
        self.assertEqual(len(TASKS), 24)
        self.assertEqual(TASKS["code-a"]["expected"], "3")
        self.assertEqual(TASKS["code-b"]["expected"], "2")
        self.assertEqual(len({r["prompt"] for r in TASKS.values()}), 24)

    def test_missing_observations_do_not_make_fake_success(self):
        result = mod.evaluate(TASKS, {}, SHA)
        self.assertEqual(result["status"], "incomplete_or_unresolved")
        self.assertEqual(result["paired_outcomes"]["not_scored"], 24)
        self.assertFalse(result["promotion_authorized"])

    def test_completed_fixtures_are_not_billing_or_live_routing_evidence(self):
        result = mod.evaluate(TASKS, self.read_lines(all_rows().values()), SHA)
        self.assertEqual(result["status"], "complete_fixture_only")
        self.assertEqual(result["paired_outcomes"]["both_correct"], 24)
        self.assertIsNone(result["reported_cost_usd"])
        self.assertFalse(result["provider_calls_independently_verified"])
        self.assertFalse(result["routing_quality_measured"])
        self.assertFalse(result["realized_savings_measured"])
        self.assertFalse(result["promotion_authorized"])

    def test_exact_answer_scoring_and_paired_disagreement(self):
        rows = all_rows()
        rows[("code-a", mod.APPROVED_MODELS[1])] = row("code-a", mod.APPROVED_MODELS[1], answer="2")
        rows[("code-b", mod.APPROVED_MODELS[0])] = row("code-b", mod.APPROVED_MODELS[0], answer=" 3 ")
        result = mod.evaluate(TASKS, rows, SHA)
        self.assertEqual(result["paired_outcomes"]["sol_only_correct"], 1)
        self.assertEqual(result["paired_outcomes"]["luna_only_correct"], 1)
        self.assertEqual(result["paired_outcomes"]["both_correct"], 22)

    def test_unknown_attempt_preserves_cost_uncertainty(self):
        rows = all_rows()
        rows[("code-a", mod.APPROVED_MODELS[1])] = row("code-a", mod.APPROVED_MODELS[1],
              status="uncertain", answer=None, input_tokens=None, output_tokens=None, elapsed_ms=None)
        result = mod.evaluate(TASKS, rows, SHA)
        self.assertEqual(result["status"], "incomplete_or_unresolved")
        self.assertEqual(result["paired_outcomes"]["not_scored"], 1)
        self.assertEqual(result["models"][mod.APPROVED_MODELS[1]]["uncertain"], 1)

    def test_wrong_plan_prompt_and_model_rejected(self):
        base = row("code-a", mod.APPROVED_MODELS[0])
        for mutation in ({"plan_sha256": "f" * 64}, {"prompt_sha256": "f" * 64},
                         {"model_id": "other:cheap"}, {"routing_applied": True},
                         {"answer": None}, {"input_tokens": -1}, {"elapsed_ms": float("inf")}):
            with self.subTest(mutation=mutation):
                with self.assertRaises(mod.InvalidEvidence):
                    self.read_lines([{**base, **mutation}])

    def test_duplicate_pairs_or_attempt_ids_rejected(self):
        baseline = row("code-a", mod.APPROVED_MODELS[0])
        with self.assertRaisesRegex(mod.InvalidEvidence, "duplicate"):
            self.read_lines([baseline, baseline])
        second = row("code-b", mod.APPROVED_MODELS[0], attempt_id=baseline["attempt_id"])
        with self.assertRaisesRegex(mod.InvalidEvidence, "attempt"):
            self.read_lines([baseline, second])

    def test_mixed_fixture_and_direct_evidence_rejected(self):
        first = row("code-a", mod.APPROVED_MODELS[0])
        second = row("code-b", mod.APPROVED_MODELS[0], evidence_mode="observed_direct")
        with self.assertRaisesRegex(mod.InvalidEvidence, "mixed"):
            self.read_lines([first, second])

    def test_complete_direct_labels_do_not_self_verify_provider_receipts(self):
        rows = all_rows()
        direct = [{**r, "evidence_mode": "observed_direct"} for r in rows.values()]
        result = mod.evaluate(TASKS, self.read_lines(direct), SHA)
        self.assertEqual(result["status"], "complete_observed_direct_unverified")
        self.assertFalse(result["provider_calls_independently_verified"])


if __name__ == "__main__":
    unittest.main()
