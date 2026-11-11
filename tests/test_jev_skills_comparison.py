"""Offline contract tests for the stand-alone shadow comparison gate."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "evaluation/jev_skills_comparison/compare.py"
spec = importlib.util.spec_from_file_location("jev_skills_shadow_compare", PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
CASES = mod.read_cases(ROOT / "evaluation/jev_skills_comparison/cases.json")
OLD = "a" * 40
NEW = "b" * 40


def observation(case_id, arm, **overrides):
    base = {
        "case_id": case_id, "arm": arm,
        "source_sha": OLD if arm == "switchyard" else NEW,
        "mode": "shadow", "applied": False, "would_switch": False,
        "proposed_model": None, "egress": "none", "latency_ms": 42,
        "external_calls": 0,
    }
    return {**base, **overrides}


class ComparisonTests(unittest.TestCase):
    def test_pinned_and_high_risk_cases_in_fixture(self):
        for case_id in ("manual-model-pin", "security-change", "irreversible-action",
                        "private-message", "restricted-content", "cross-provider", "tool-dependent"):
            self.assertFalse(CASES[case_id]["may_switch"])

    def test_empty_evidence_is_incomplete(self):
        result = mod.compare(CASES, {})
        self.assertEqual(result["status"], "incomplete_or_blocked")
        self.assertEqual(len(result["arms"]["switchyard"]["missing"]), len(CASES))
        self.assertFalse(result["promotion_authorized"])

    def test_complete_offline_screen_is_not_promotion(self):
        rows = {(key, arm): observation(key, arm) for key in CASES for arm in mod.ARMS}
        rows[("routine-extract", "jev-skills")] = observation(
            "routine-extract", "jev-skills", would_switch=True,
            proposed_model="same-provider:small")
        result = mod.compare(CASES, rows)
        self.assertEqual(result["status"], "complete_synthetic_shadow_screen")
        self.assertEqual(result["arms"]["jev-skills"]["switch_proposals"], 1)
        self.assertFalse(result["promotion_authorized"])

    def test_unsafe_proposal_blocks(self):
        rows = {(key, arm): observation(key, arm) for key in CASES for arm in mod.ARMS}
        rows[("security-change", "jev-skills")] = observation(
            "security-change", "jev-skills", would_switch=True,
            proposed_model="other-provider:tiny")
        result = mod.compare(CASES, rows)
        self.assertEqual(result["status"], "incomplete_or_blocked")
        self.assertEqual(result["arms"]["jev-skills"]["policy_violations"], ["security-change"])

    def test_privacy_violation_blocks(self):
        rows = {(key, arm): observation(key, arm) for key in CASES for arm in mod.ARMS}
        rows[("restricted-content", "switchyard")] = observation(
            "restricted-content", "switchyard", egress="redacted_text", external_calls=1)
        result = mod.compare(CASES, rows)
        self.assertEqual(result["status"], "incomplete_or_blocked")
        self.assertEqual(result["arms"]["switchyard"]["privacy_violations"], ["restricted-content"])

    def test_invalid_and_duplicate_observations_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            f = Path(temp) / "records.jsonl"
            valid = observation("routine-extract", "jev-skills")
            f.write_text(json.dumps(valid) + "\n" + json.dumps(valid) + "\n")
            with self.assertRaisesRegex(mod.InvalidEvidence, "duplicate"):
                mod.read_observations(f, CASES)
            for patch in ({"mode": "on"}, {"applied": True}, {"source_sha": "unknown"},
                          {"latency_ms": -1}, {"external_calls": -1},
                          {"would_switch": True, "proposed_model": None}, {"egress": "raw"}):
                with self.subTest(patch=patch):
                    f.write_text(json.dumps({**valid, **patch}) + "\n")
                    with self.assertRaises(mod.InvalidEvidence):
                        mod.read_observations(f, CASES)

    def test_evidence_is_not_invented(self):
        rows = {("routine-extract", "jev-skills"): observation("routine-extract", "jev-skills")}
        result = mod.compare(CASES, rows)
        self.assertEqual(result["arms"]["switchyard"]["observed"], 0)
        self.assertEqual(result["arms"]["jev-skills"]["observed"], 1)
        self.assertEqual(result["arms"]["jev-skills"]["latency_ms_total"], 42)


if __name__ == "__main__":
    unittest.main()
