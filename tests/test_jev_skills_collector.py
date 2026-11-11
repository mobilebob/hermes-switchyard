"""Collector boundaries; generated fixture answers are never model measurements."""
import hashlib
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1] / "evaluation/jev_skills_comparison"
spec = importlib.util.spec_from_file_location("shadow_collector", HERE / "collect.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
gate = sys.modules["compare"]
CATALOG = collector.load_catalog(HERE / "catalog.json")
CASES = gate.read_cases(HERE / "cases.json")


def evidence(case_id="routine-extract", arm="switchyard", **updates):
    case = CASES[case_id]
    return {
        "schema_version": 2, "case_id": case_id, "arm": arm,
        "source_sha": collector.PINS[arm], "mode": "shadow", "applied": False,
        "evidence_kind": "offline_fixture", "current_model": CATALOG["current"],
        "approved_candidates": ["fixture:small", "fixture:large"],
        "task_sha256": hashlib.sha256(case["task"].encode()).hexdigest(),
        "catalog_sha256": collector.digest(CATALOG),
        "proposed_model": "fixture:small", "would_switch": True,
        "egress": "none", "external_calls": 0, "provider_requests": [],
        "simulated_requests": 1, "latency_ms": 10,
        "abstained": False, "abstention_reason": None,
        "safety_decision": "allowed_shadow_only" if case["may_switch"] else "rejected",
        "safety_reason": None if case["may_switch"] else "case_policy_forbids_switch",
        "worker_failed": False, "implementation_status": "selected",
        "network_attempts": 0, "process_attempts": 0, "attempt_accounting": "observed_python_guards",
        **updates,
    }


class CollectorBoundaryTests(unittest.TestCase):
    def read(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "observations.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in rows))
            return gate.read_observations(path, CASES)

    def test_fixture_results_are_not_inference_or_promotion(self):
        rows = [evidence(key, arm, proposed_model=None, would_switch=False,
                         abstained=True, safety_decision="abstained", safety_reason="implementation_abstained")
                for key in CASES for arm in gate.ARMS]
        report = gate.compare(CASES, self.read(rows))
        self.assertEqual(report["status"], "offline_fixture_only")
        self.assertFalse(report["promotion_authorized"])
        self.assertEqual(report["arms"]["switchyard"]["external_calls"], 0)
        self.assertEqual(report["arms"]["switchyard"]["abstentions"], 12)

    def test_no_response_is_not_complete_measurement(self):
        rows = [evidence(key, arm, evidence_kind="offline_no_response", proposed_model=None,
                         would_switch=False, abstained=True, safety_decision="abstained",
                         safety_reason="implementation_abstained")
                for key in CASES for arm in gate.ARMS]
        report = gate.compare(CASES, self.read(rows))
        self.assertEqual(report["status"], "offline_no_response_only")

    def test_v2_rejects_fabricated_provenance_and_incomparable_inputs(self):
        for update in ({"source_sha": "a" * 40}, {"catalog_sha256": "a" * 64},
                       {"task_sha256": "b" * 64}, {"approved_candidates": ["fixture:outsider"]},
                       {"current_model": "fixture:outsider"}, {"evidence_kind": "live"},
                       {"provider_requests": [{}]}, {"external_calls": 1}, {"egress": "redacted_text"},
                       {"proposed_model": "remote:other"}, {"simulated_requests": -1},
                       {"would_switch": False}, {"abstained": "no"}):
            with self.subTest(update=update), self.assertRaises(gate.InvalidEvidence):
                self.read([evidence(**update)])

    def test_mixed_fixture_modes_rejected(self):
        with self.assertRaises(gate.InvalidEvidence):
            self.read([evidence(), evidence("routine-format", evidence_kind="offline_no_response")])

    def test_failures_block_even_with_complete_coverage(self):
        rows = [evidence(key, arm, proposed_model=None, would_switch=False, abstained=True,
                         safety_decision="abstained", safety_reason="implementation_abstained")
                for key in CASES for arm in gate.ARMS]
        rows[0]["worker_failed"] = True
        report = gate.compare(CASES, self.read(rows))
        self.assertEqual(report["status"], "incomplete_or_blocked")
        self.assertEqual(report["arms"]["switchyard"]["worker_failures"], ["routine-extract"])

    def test_unsafe_raw_proposals_not_hidden_by_safety_envelope(self):
        row = evidence("security-change")
        report = gate.compare(CASES, self.read([row]))
        self.assertEqual(report["arms"]["switchyard"]["policy_violations"], ["security-change"])

    def test_source_pin_and_dirty_checkout_block_before_workers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for results in ([str(root), "f" * 40], [str(root), collector.PINS["switchyard"], "?? injected.py"]):
                with patch.object(collector.subprocess, "check_output", side_effect=results):
                    with self.assertRaises(ValueError):
                        collector.verify_source(root, "switchyard")

    def test_credentials_are_not_inherited(self):
        with patch.dict(os.environ, {"SECRET_API_KEY": "not-real", "PYTHONPATH": "untrusted", "HERMES_PROFILE": "other"}):
            env = collector.worker_env(Path("/isolated"))
        self.assertNotIn("SECRET_API_KEY", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertEqual(env["HERMES_PROFILE"], "default")
        self.assertEqual(env["HOME"], "/isolated")

    def test_fixture_approval_cannot_authorize_live_models(self):
        for update in ({"provider": "openrouter"}, {"scope": "live"}, {"current": "fixture:unknown"}):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "catalog.json"
                path.write_text(json.dumps({**CATALOG, **update}))
                with self.assertRaises(ValueError):
                    collector.load_catalog(path)

    def test_safety_rejects_cross_provider_and_preserves_case_policy(self):
        self.assertEqual(collector.safety(CASES["routine-extract"], "other:small", CATALOG, False)[0], "rejected")
        self.assertEqual(collector.safety(CASES["manual-model-pin"], "fixture:small", CATALOG, False)[0], "rejected")
        self.assertEqual(collector.safety(CASES["routine-extract"], "fixture:small", CATALOG, False)[0], "allowed_shadow_only")

    def test_live_mode_rejected_before_output(self):
        with self.assertRaises(ValueError):
            collector.collect({}, Path("not-created"), "live", HERE / "catalog.json")

    def test_collected_escape_attempts_and_missing_accounting_rejected(self):
        for update in ({"network_attempts": 1}, {"process_attempts": 1}, {"network_attempts": None},
                       {"process_attempts": True}, {"attempt_accounting": "unknown_worker_failed"}):
            with self.subTest(update=update), self.assertRaises(gate.InvalidEvidence):
                self.read([evidence(**update)])

    def test_failed_worker_unknown_counters_are_not_treated_as_success(self):
        row = evidence(worker_failed=True, network_attempts=None, process_attempts=None,
                       attempt_accounting="unknown_worker_failed")
        report = gate.compare(CASES, self.read([row]))
        self.assertEqual(report["status"], "incomplete_or_blocked")

    def test_collector_schema_cannot_be_downgraded_to_legacy(self):
        row = evidence(source_sha="a" * 40)
        del row["schema_version"]
        del row["evidence_kind"]
        with self.assertRaises(gate.InvalidEvidence):
            self.read([row])

    def test_frozen_case_set_and_policy_required_for_collector(self):
        with self.assertRaises(gate.InvalidEvidence):
            gate.compare({"routine-extract": CASES["routine-extract"]}, {("routine-extract", "switchyard"): evidence()})
        changed = {**CASES, "routine-extract": {**CASES["routine-extract"], "may_switch": False}}
        with self.assertRaises(gate.InvalidEvidence):
            gate.compare(changed, {("routine-extract", "switchyard"): evidence()})

    def test_changed_catalog_description_rejected_before_workers(self):
        value = json.loads(json.dumps(CATALOG))
        value["models"][0]["description"] = "changed"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "frozen"):
                collector.load_catalog(path)

    def test_cli_status_distinguishes_offline_success_from_blocked(self):
        args = ["--switchyard-source", "unused", "--jev-skills-source", "unused",
                "--offline", "no-response", "--output", "unused"]
        for status, expected in (("offline_no_response_only", 0), ("offline_fixture_only", 0), ("incomplete_or_blocked", 1)):
            with self.subTest(status=status), patch.object(collector, "collect", return_value={"status": status}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(collector.main(args), expected)


if __name__ == "__main__":
    unittest.main()
