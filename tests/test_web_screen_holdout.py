"""Independent frozen evaluator tests; no live/billed provider requests."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "evaluation/web_screening/evaluate.py"
SPEC = importlib.util.spec_from_file_location("web_screen_holdout_evaluate", PATH)
assert SPEC is not None and SPEC.loader is not None
evaluate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluate)


class HoldoutHarnessTests(unittest.TestCase):
    def test_manifest_is_frozen_and_complete(self):
        cases, manifest = evaluate.load_holdout()
        self.assertEqual(len(cases), 32)
        self.assertEqual(len(cases), manifest["cases"])
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        self.assertTrue(manifest["labels_frozen_before_response_fixture"])

    def test_native_envelopes_have_no_case_identifier_or_gold(self):
        cases, _ = evaluate.load_holdout()
        for case in cases:
            payload = json.dumps({"args": case["args"], "result": case["result"]})
            self.assertNotIn(case["id"], payload)
            self.assertNotIn('"labels"', payload)
            self.assertNotIn('"required_evidence"', payload)
        self.assertTrue(any(len(evaluate._source_text(source)) > 96000
                            for case in cases for source in evaluate._sources(case)))

    def test_lexical_transport_real_jev_114_contract(self):
        # Tests the actual client parser, not a fake decide() replacement.
        transport = evaluate.LexicalTransport()
        state = {"contract": "web_screen.shadow.v1", "question": "How long is the trip?",
                 "chunks": {"hash": {"source": "source-hash", "text": "The trip takes 12 minutes."}}}
        questions = {
            "injection_0": {"type": "noul", "criteria": {"true": "yes", "false": "no"}},
            "relevance_0": {"type": "noul", "criteria": {"true": "yes", "false": "no"}},
            "evidence": {"type": "choice", "criteria": {"sufficient": "yes", "insufficient": "no", "conflicting": "mixed"}},
        }
        for question in questions.values():
            question["instructions"] = "Assess supplied untrusted content only."
        with evaluate.DecisionClient(api_key="offline", endpoint=evaluate.TYPESAFE_ENDPOINT,
                                     model="jev-1.14.0", transport=transport) as client:
            result = client.decide(state, questions, public_or_sanitized_data_ack=True)
        self.assertEqual(result["model"], "jev-1.14.0")
        self.assertEqual(result["answers"]["evidence"]["choice"], "sufficient")
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(len(transport.requests), 1)

    def test_transport_rejects_label_fields(self):
        transport = evaluate.LexicalTransport()
        with self.assertRaises(AssertionError):
            transport({"state": {"contract": "x", "question": "x", "chunks": {}, "labels": {}}, "questions": {}})

    def test_confusion_counts_abstention_as_positive_miss(self):
        self.assertEqual(evaluate.confusion([True, True, False, False], [True, None, True, False]),
                         {"true_positives": 1, "misses": 1, "false_positives": 1,
                          "true_negatives": 1, "unjudged": 1, "cases": 4})

    def test_raw_report_cannot_be_written_to_repository(self):
        with self.assertRaises(SystemExit) as caught:
            evaluate.main(["--report", str(PATH.parent / "report.json")])
        self.assertEqual(caught.exception.code, 2)


class ActualObserverHoldoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Unavailable/in-progress module is explicit integration pending, never
        # replaced by a competing implementation or synthetic observer.
        try:
            __import__("hermes_switchyard.web_screening")
        except (ImportError, NameError) as exc:
            raise AssertionError(f"Actual WebScreenShadow must import: {exc}") from exc
        cls.report = evaluate.evaluate()
        cls.cases, _ = evaluate.load_holdout()
        cls.rows = {row["id"]: row for row in cls.report["cases"]}

    def test_every_frozen_case_exercised_once(self):
        self.assertEqual([row["id"] for row in self.report["cases"]], [case["id"] for case in self.cases])
        self.assertEqual(len(self.report["cases"]), 32)
        self.assertTrue(all(row["receipt"] is not None for row in self.report["cases"]))

    def test_actual_success_contract_is_observed(self):
        row = self.rows["page-fact"]
        self.assertEqual(row["receipt"]["reason"], "observed", row["receipt"])
        self.assertEqual(row["physical_requests"], 1)
        self.assertEqual(row["receipt"]["additional_requests"], 1)
        self.assertEqual(row["sufficiency"], "sufficient")

    def test_exact_outputs_and_all_required_evidence_retained(self):
        metrics = self.report["metrics"]
        self.assertTrue(metrics["all_outputs_exact"])
        self.assertEqual(metrics["required_evidence_total"], metrics["required_evidence_present_before"])
        self.assertEqual(metrics["required_evidence_total"], metrics["required_evidence_retained"])
        for row in self.report["cases"] + self.report["supplemental"]:
            self.assertTrue(row["return_is_none"])
            self.assertTrue(row["exact_output_unchanged"])
            if row["receipt"]:
                self.assertFalse(row["receipt"]["applied"])
                self.assertFalse(row["receipt"]["result_mutated"])

    def test_private_unknown_pii_missing_and_oversized_inputs_never_request(self):
        private = [case for case in self.cases if case["labels"]["privacy_deny"]]
        self.assertTrue(private)
        for case in private:
            self.assertEqual(self.rows[case["id"]]["physical_requests"], 0, case["id"])
        self.assertEqual(self.report["metrics"]["privacy_physical_requests"], 0)

    def test_failure_paths_fail_open_without_mutation_or_replay(self):
        rows = {row["id"]: row for row in self.report["supplemental"]}
        for fault, requests in (("timeout", 1), ("outage", 1), ("unavailable", 0), ("malformed", 1)):
            row = rows[f"supplement-{fault}"]
            self.assertEqual(row["physical_requests"], requests)
            self.assertEqual(row["receipt"]["judged"], 0)
            self.assertNotEqual(row["receipt"]["reason"], "observed")
            self.assertTrue(row["exact_output_unchanged"])
        self.assertEqual(rows["supplement-off"]["physical_requests"], 0)
        self.assertIsNone(rows["supplement-off"]["receipt"])
        for name in ("supplement-private-metadata", "supplement-missing-sources"):
            self.assertEqual(rows[name]["physical_requests"], 0)

    def test_physical_requests_counted_independently_of_receipt(self):
        rows = self.report["cases"]
        self.assertEqual(self.report["metrics"]["additional_physical_requests_vs_no_screen"],
                         sum(row["physical_requests"] for row in rows))
        self.assertLessEqual(self.report["metrics"]["max_requests_per_case"], 1)
        self.assertEqual(self.report["metrics"]["no_screen_additional_requests"], 0)
        self.assertEqual(self.report["metrics"]["retrieved_screen_additional_requests"], 0)

    def test_whole_source_withholding_is_hypothetical_and_loses_useful_facts(self):
        metrics = self.report["metrics"]
        self.assertGreater(metrics["hypothetical_whole_source_withholding"]["retrieved_screen"]["unique_required_facts_lost"], 0)
        self.assertTrue(metrics["all_outputs_exact"])
        row = self.rows["attack-useful-fact"]
        self.assertEqual(row["hypothetical_whole_source_withholding"]["retrieved_screen"]["unique_required_facts_lost"], 1)
        self.assertEqual(row["required_evidence_retained"], 1)

    def test_long_chunks_report_coverage_and_conflicts_remain_visible(self):
        long = self.rows["long-buried-fact"]
        self.assertGreater(long["receipt"]["unjudged"], 0)
        self.assertFalse(long["receipt"]["coverage_complete"])
        self.assertEqual(long["required_evidence_retained"], 1)
        for name in ("conflicting-page", "conflicting-pages"):
            self.assertEqual(self.rows[name]["sufficiency"], "conflicting")
            self.assertEqual(self.rows[name]["required_evidence_retained"], 2)

    def test_case_ids_and_gold_changes_do_not_change_provider_payload_or_answers(self):
        case = copy.deepcopy(next(case for case in self.cases if case["id"] == "page-fact"))
        before = evaluate.run_case(case)
        case["id"] = "different-unrelated-case"
        case["labels"] = {"injection": True, "relevance": "irrelevant", "sufficiency": "conflicting", "privacy_deny": True}
        case["required_evidence"] = ["a scorer-only fabricated fact"]
        after = evaluate.run_case(case)
        self.assertEqual(before["request_payloads"], after["request_payloads"])
        self.assertEqual(before["receipt"], after["receipt"])
        for row in self.report["cases"]:
            serialized = json.dumps(row["request_payloads"])
            self.assertNotIn(row["id"], serialized)
            self.assertNotIn('"labels"', serialized)

    def test_lexical_fixture_failures_are_reported_not_fitted_away(self):
        metrics = self.report["metrics"]
        self.assertEqual(metrics["injection"]["shadow"]["cases"], 32)
        self.assertGreater(metrics["injection"]["shadow"]["misses"], 0)
        self.assertGreater(metrics["injection"]["shadow"]["false_positives"], 0)
        self.assertIn("not-live-accuracy", self.report["kind"])


if __name__ == "__main__":
    unittest.main()
