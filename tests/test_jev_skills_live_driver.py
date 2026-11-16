"""Offline live-driver tests: fake HTTPS, synthetic evidence and temporary ledgers."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]/"evaluation/jev_skills_comparison"
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("stage_a_bounded_driver", HERE/"live_driver.py")
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
spec = importlib.util.spec_from_file_location("stage_a_billing", HERE/"billing_evidence.py")
assert spec is not None and spec.loader is not None
billing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(billing)
CASES = mod.read_cases(HERE/"cases.json")
CREDENTIAL = "offline-not-a-real-credential"


def evidence(directory):
    directory.mkdir()
    texts = {
        "billing": "$0.042 per million input tokens. Output tokens are free. Same pricing applies to every account. No per-request, failed-request or minimum fees.",
        "models": "jev-1.14.0 $0.042 64k per-request 32k per-question. Jev ingests the `state` once. The `state` plus all questions combined. Long context is off by default. TypeSafe-Danger-Zone-Long-Context. 262k tokens.",
    }
    records = []
    for name, text in texts.items():
        (directory/(name+"-source")).write_text(text)
        (directory/(name+".txt")).write_text(text)
        url = billing.BILLING_URL if name == "billing" else billing.MODELS_URL
        records.append({"name": name, "url": url, "resolved_url": url, "sha256": mod.sha(text.encode()), "retrieved_at": "offline-fixture"})
    (directory/"billing-provenance.json").write_text(json.dumps(records))


def plan():
    entries = []
    for key, case in CASES.items():
        if case["egress_limit"] != "redacted_text":
            continue
        for arm in mod.PINS:
            body = {"model": "jev-1.14.0", "state": {"task" if arm == "switchyard" else "user_turn": case["task"]},
                    "questions": {"fit": {"type": "noul", "instructions": "Public synthetic fixture"}}}
            entries.append({"case_id": key, "arm": arm, "source_sha": mod.PINS[arm],
                "task_sha256": mod.sha(case["task"].encode()), "payload": body, "body_sha256": mod.sha(mod.canonical(body))})
    return {"catalog": mod.CATALOG, "real_provider_requests": 0, "entries": entries}


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.docs = self.root/"docs"
        evidence(self.docs)
        self.plan = plan()
        self.plan_path = self.root/"plan.json"
        self.plan_path.write_text(json.dumps(self.plan))
        self.output = self.root/"trial"
        self.roots = {arm: self.root/arm for arm in mod.PINS}
        self.pinning = patch.object(mod, "verify_source", side_effect=lambda p, a: {"source_sha": mod.PINS[a], "clean": True})
        self.pinning.start()
        self.addCleanup(self.pinning.stop)
        mod.prepare(self.output, self.plan_path, self.docs, self.roots, CREDENTIAL)

    def native(self, payload, handler, workspace):
        key = payload["case"]["id"]
        body = next(e["payload"] for e in self.plan["entries"] if e["case_id"] == key and e["arm"] == payload["arm"])
        try:
            handler(body)
            proposed = "openai-codex:gpt-6-luna" if CASES[key]["may_switch"] else mod.CATALOG["current"]
            reason = None
        except (mod.Blocked, mod.Uncertain):
            proposed, reason = None, "shadow_no_retry"
        return {"network_attempts": 0, "process_attempts": 0, "worker_failed": False,
                "task_received": payload["case"]["task"], "candidate_ids": ["openai-codex:"+m["model"] for m in mod.CATALOG["models"]],
                "proposed_model": proposed, "abstained": proposed is None, "abstention_reason": reason,
                "raw_decision": {"fixture": True}}

    def fake_http(self, status=200):
        patcher = patch("http.client.HTTPSConnection")
        connection = patcher.start()
        self.addCleanup(patcher.stop)
        response = connection.return_value.getresponse.return_value
        response.status = status
        response.read.return_value = mod.canonical({"model": "jev-1.14.0", "answers": {"fit": {"type": "noul", "noul": .9}}, "usage": {"input_tokens": 100}})
        return connection

    def test_valid_first_party_clauses_produce_bounded_reservation_proof(self):
        result = billing.verify_billing(self.docs)
        self.assertEqual(result["conservative_per_request_upper_micro_usd"], 11011)
        self.assertEqual(result["all_request_reservations_micro_usd"], 480000)
        self.assertEqual(result["authorized_micro_usd"], 500000)
        self.assertFalse(result["extended_context_header_sent"])

    def test_boolean_or_modified_summary_does_not_replace_raw_evidence(self):
        (self.docs/"billing.txt").write_text("price_ceiling_verified=true")
        with self.assertRaises(mod.Blocked):
            billing.verify_billing(self.docs)
        (self.docs/"billing-source").write_text("a different tariff")
        with self.assertRaises(mod.Blocked):
            billing.verify_billing(self.docs)

    def test_no_ledger_or_call_exists_after_prepare(self):
        self.assertFalse((self.output/"budget.sqlite").exists())
        manifest = json.loads((self.output/"manifest.json").read_text())
        self.assertEqual(manifest["max_external_requests"], 24)
        self.assertEqual(manifest["main_model_calls_authorized"], 0)

    def test_altered_missing_or_private_plan_entries_rejected(self):
        for mode in ("missing", "task", "source", "duplicate", "catalog"):
            value = copy.deepcopy(self.plan)
            if mode == "missing":
                value["entries"].pop()
            elif mode == "task":
                value["entries"][0]["payload"]["state"]["task"] = "private substituted text"
            elif mode == "source":
                value["entries"][0]["source_sha"] = "a"*40
            elif mode == "duplicate":
                value["entries"].append(value["entries"][0])
            else:
                value["catalog"] = {}
            with self.subTest(mode=mode), self.assertRaises(mod.Blocked):
                mod.verify_plan(value, CASES)

    def test_all_eligible_cases_share_one_ledger_and_resume_never_replays(self):
        connection = self.fake_http()
        with patch.object(mod, "native_decision", side_effect=self.native):
            result = mod.execute(self.output, CREDENTIAL, initialize=True)
            again = mod.execute(self.output, CREDENTIAL)
        self.assertEqual(connection.return_value.request.call_count, 20)
        self.assertEqual(result, again)
        self.assertEqual(result["status"], "complete_live_decision_only_shadow_screen")
        self.assertEqual(result["ledger"]["reserved_requests"], 20)
        self.assertEqual(result["ledger"]["reserved_micro_usd"], 400000)
        self.assertEqual(result["ledger"]["unresolved_or_failed_requests"], 0)
        self.assertIsNone(result["ledger"]["billed_cost_micro_usd"])
        for call in connection.return_value.request.call_args_list:
            self.assertNotIn("TypeSafe-Danger-Zone-Long-Context", call.kwargs["headers"])

    def test_first_failure_stops_entire_trial_and_failed_case_is_not_replayed(self):
        connection = self.fake_http(429)
        with patch.object(mod, "native_decision", side_effect=self.native):
            result = mod.execute(self.output, CREDENTIAL, initialize=True)
            mod.execute(self.output, CREDENTIAL)
        self.assertEqual(connection.return_value.request.call_count, 1)
        self.assertEqual(result["ledger"]["reserved_requests"], 1)
        self.assertEqual(result["ledger"]["reserved_micro_usd"], 20000)
        self.assertEqual(result["status"], "blocked_live_decision_only_shadow_screen")

    def test_changed_prerequisite_blocks_before_any_dispatch(self):
        connection = self.fake_http()
        with patch.object(mod, "runner_hashes", return_value={"changed": True}):
            with self.assertRaises(mod.Blocked):
                mod.execute(self.output, CREDENTIAL, initialize=True)
        connection.assert_not_called()
        self.assertFalse((self.output/"budget.sqlite").exists())

    def test_unresolved_prior_intent_is_counted_not_replayed(self):
        proof = json.loads((self.output/"proof.json").read_text())
        approved = mod.ApprovedPayloads(CASES, self.plan["entries"], proof)
        ledger = mod.BudgetLedger(self.output/"budget.sqlite", approved.binding+":"+proof["account_reference"], create=True)
        ledger.reserve("switchyard", "routine-extract", mod.body_for(self.plan["entries"][0]["payload"]))
        ledger.close()
        connection = self.fake_http()
        with patch.object(mod, "native_decision", side_effect=self.native):
            result = mod.execute(self.output, CREDENTIAL)
        connection.assert_not_called()
        self.assertEqual(result["ledger"]["reserved_requests"], 1)
        self.assertEqual(result["ledger"]["unresolved_or_failed_requests"], 1)
        self.assertEqual(result["status"], "blocked_live_decision_only_shadow_screen")


if __name__ == "__main__":
    unittest.main()
