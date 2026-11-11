"""Offline tests: simulated proofs, fake HTTPS, and disposable budget ledgers only."""
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parents[1] / "evaluation/jev_skills_comparison"
spec = importlib.util.spec_from_file_location("stage_a_live_transport", HERE / "live_transport.py")
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
sys.modules.setdefault("live_transport", mod)
spec = importlib.util.spec_from_file_location("stage_a_live_preflight", HERE / "live_preflight.py")
assert spec is not None and spec.loader is not None
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)
CASES = {c["id"]: c for c in json.loads((HERE / "cases.json").read_text())["cases"]}
CREDENTIAL = "offline-not-a-real-credential"


def proof(**changes):
    return {"account_verified": True, "model_allowlist_verified": True,
            "public_payloads_verified": True, "price_ceiling_verified": True,
            "account_reference": "offline-account", "allowlist_reference": "offline-reviewed-models",
            "payload_review_reference": "offline-full-body-review", "pricing_reference": "offline-account-bound-price",
            "credential_sha256": mod.sha(CREDENTIAL.encode()),
            "provider": "typesafe", "model": mod.MODEL,
            "rate_micro_usd_per_million": mod.RATE_MICRO_USD_PER_MILLION,
            "output_free": True, "no_other_fees": True,
            "billable_input_token_ceiling": mod.TOKEN_CEILING, **changes}


def entry(case_id="routine-extract", arm="switchyard", **updates):
    task = CASES[case_id]["task"]
    payload = {"model": mod.MODEL, "state": {"task" if arm == "switchyard" else "user_turn": task},
               "questions": {"fit": {"type": "noul", "instructions": "The public task is routine"}}}
    return {"case_id": case_id, "arm": arm, "source_sha": mod.PINS[arm],
            "task_sha256": mod.sha(task.encode()), "payload": payload,
            "body_sha256": mod.sha(mod.canonical(payload)), **updates}


class Response:
    status = 200
    def __init__(self, payload=None):
        self.payload = payload or {"model": mod.MODEL, "answers": {"fit": {"confidence": .9, "value": True}},
                                   "usage": {"input_tokens": 100}}
    def read(self, limit):
        return mod.canonical(self.payload)[:limit]


class LiveTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "budget.sqlite"
        self.entries = [entry(), entry(arm="jev-skills")]
        self.approved = mod.ApprovedPayloads(copy.deepcopy(CASES), self.entries, proof())
        self.binding = self.approved.binding + ":offline-account"
        self.ledger = mod.BudgetLedger(self.path, self.binding, create=True)
        self.addCleanup(self.ledger.close)
        self.transport = mod.TypeSafeShadowTransport(api_key=CREDENTIAL, account_reference="offline-account",
                                                    approved=self.approved, ledger=self.ledger)

    def test_missing_prerequisite_blocks_without_network_or_ledger_use(self):
        for field in ("account_verified", "model_allowlist_verified", "public_payloads_verified", "price_ceiling_verified"):
            with self.subTest(field=field), patch.object(mod.http.client, "HTTPSConnection") as connection:
                with self.assertRaises(mod.Blocked):
                    mod.ApprovedPayloads(CASES, self.entries, proof(**{field: False}))
                connection.assert_not_called()
        self.assertEqual(self.ledger.summary()["reserved_requests"], 0)

    def test_public_price_estimate_is_not_enforceable_billing_proof(self):
        for changes in ({"rate_micro_usd_per_million": 42001}, {"output_free": False},
                        {"no_other_fees": False}, {"billable_input_token_ceiling": 65536},
                        {"pricing_reference": ""},
                        {"owner_waived_hard_spend_ceiling": True},
                        {"owner_waived_hard_spend_ceiling": True, "price_ceiling_verified": False}):
            with self.subTest(changes=changes), self.assertRaises(mod.Blocked):
                mod.validate_prerequisites(proof(**changes))

    def test_fixed_route_and_strict_json(self):
        for payload in ({**self.entries[0]["payload"], "provider": {}},
                        {**self.entries[0]["payload"], "model": "jev-latest"},
                        {**self.entries[0]["payload"], "assets": []}):
            with self.assertRaises(mod.Blocked):
                mod.body_for(payload)
        for body in ('{"x":1,"x":2}', '{"x":NaN}'):
            with self.assertRaises(mod.Blocked):
                mod.strict_loads(body)
        payload = copy.deepcopy(self.entries[0]["payload"])
        payload["questions"] = {str(i): {"type": "noul", "instructions": "Public fixture"} for i in range(4)}
        with self.assertRaises(mod.Blocked):
            mod.body_for(payload)
        for forbidden in ("assets", "image_url", "password", "api_key"):
            payload = copy.deepcopy(self.entries[0]["payload"])
            payload["state"]["nested"] = {forbidden: "prohibited fixture value"}
            with self.subTest(field=forbidden), self.assertRaises(mod.Blocked):
                mod.body_for(payload)

    def test_private_cases_rejected_even_if_full_body_is_approved(self):
        for key in ("private-message", "restricted-content"):
            for arm in mod.ARMS:
                with self.subTest(case=key, arm=arm), self.assertRaises(mod.Blocked):
                    mod.ApprovedPayloads(CASES, [entry(key, arm)], proof())

    def test_prohibited_mutations_anywhere_in_payload_rejected_before_reservation(self):
        for section in ("state", "questions"):
            payload = copy.deepcopy(self.entries[0]["payload"])
            payload[section]["injected"] = "private operational content"
            with self.subTest(section=section), patch.object(mod.http.client, "HTTPSConnection") as connection:
                with self.assertRaises(mod.Blocked):
                    self.transport.send("routine-extract", "switchyard", payload)
                connection.assert_not_called()
        self.assertEqual(self.ledger.summary()["reserved_requests"], 0)
        payload = copy.deepcopy(self.entries[0]["payload"])
        payload["questions"]["fit"]["instructions"] = CREDENTIAL
        approved = mod.ApprovedPayloads(CASES, [entry(payload=payload, body_sha256=mod.sha(mod.canonical(payload)))], proof())
        ledger = mod.BudgetLedger(Path(self.temp.name) / "credential-rejection.sqlite", approved.binding + ":offline-account", create=True)
        try:
            broker = mod.TypeSafeShadowTransport(api_key=CREDENTIAL, account_reference="offline-account", approved=approved, ledger=ledger)
            with patch.object(mod.http.client, "HTTPSConnection") as connection:
                with self.assertRaisesRegex(mod.Blocked, "credential_in_outbound_body"):
                    broker.send("routine-extract", "switchyard", payload)
                connection.assert_not_called()
            self.assertEqual(ledger.summary()["reserved_requests"], 0)
        finally:
            ledger.close()

    def test_task_and_source_policy_cannot_be_changed_by_approval_artifact(self):
        changed = copy.deepcopy(self.entries[0])
        changed["payload"]["state"]["task"] = "private substituted task"
        changed["body_sha256"] = mod.sha(mod.canonical(changed["payload"]))
        for value in (changed, entry(source_sha="a" * 40), entry(task_sha256="b" * 64)):
            with self.assertRaises(mod.Blocked):
                mod.ApprovedPayloads(CASES, [value], proof())
        subset = {"routine-extract": CASES["routine-extract"]}
        with self.assertRaises(mod.Blocked):
            mod.ApprovedPayloads(subset, self.entries, proof())

    def test_key_and_account_are_bound_to_proof_and_ledger(self):
        for key, account in ((CREDENTIAL + "different", "offline-account"), (CREDENTIAL, "other-account")):
            with self.assertRaises(mod.Blocked):
                mod.TypeSafeShadowTransport(api_key=key, account_reference=account, approved=self.approved, ledger=self.ledger)

    def test_persistent_intent_is_visible_before_connection_construction(self):
        class Connection:
            def __init__(inner, *args, **kwargs):
                with sqlite3.connect(self.path) as c:
                    self.assertEqual(c.execute("SELECT outcome,reserved_micro_usd FROM attempts").fetchall(),
                                     [("reserved_unknown_dispatch", mod.RESERVE_MICRO_USD)])
                self.assertEqual(args, ("api.typesafe.ai",))
            def request(inner, method, path, *, body, headers):
                self.assertEqual((method, path), ("POST", "/v1/systemone"))
                self.assertEqual(headers["Connection"], "close")
                self.assertEqual(set(headers), {"Authorization", "Content-Type", "Accept", "Connection"})
                self.assertEqual(mod.strict_loads(body), self.entries[0]["payload"])
            def getresponse(inner): return Response()
            def close(inner): pass
        with patch.object(mod.http.client, "HTTPSConnection", Connection):
            self.transport.send("routine-extract", "switchyard", self.entries[0]["payload"])
        summary = self.ledger.summary()
        self.assertEqual(summary["reserved_micro_usd"], 20000)
        self.assertEqual(summary["usage_estimate_micro_usd"], 5)
        self.assertIsNone(summary["billed_cost_micro_usd"])
        self.assertEqual(summary["outcomes"], ["observed_usage"])
        self.assertNotIn(CREDENTIAL.encode(), self.path.read_bytes())

    def test_timeout_consumes_reservations_and_cannot_retry_after_reopen(self):
        with patch.object(mod.http.client, "HTTPSConnection", side_effect=TimeoutError) as connection:
            with self.assertRaises(mod.Uncertain):
                self.transport.send("routine-extract", "switchyard", self.entries[0]["payload"])
            self.assertEqual(connection.call_count, 1)
        self.assertEqual(self.ledger.summary()["outcomes"], ["uncertain"])
        other = mod.BudgetLedger(self.path, self.binding)
        try:
            with self.assertRaises(mod.Blocked):
                other.reserve("switchyard", "routine-extract", b"changed")
            self.assertEqual(other.summary()["reserved_micro_usd"], 20000)
        finally:
            other.close()

    def test_http_rate_limit_and_redirect_never_retry(self):
        for index, status in enumerate((302, 429, 529)):
            case_id = tuple(CASES)[index]
            approved = mod.ApprovedPayloads(CASES, [entry(case_id)], proof())
            path = Path(self.temp.name) / f"status-{status}.sqlite"
            ledger = mod.BudgetLedger(path, approved.binding + ":offline-account", create=True)
            try:
                transport = mod.TypeSafeShadowTransport(api_key=CREDENTIAL, account_reference="offline-account", approved=approved, ledger=ledger)
                with patch.object(mod.http.client, "HTTPSConnection") as connection:
                    connection.return_value.getresponse.return_value.status = status
                    with self.assertRaises(mod.Uncertain):
                        transport.send(case_id, "switchyard", entry(case_id)["payload"])
                    self.assertEqual(connection.return_value.request.call_count, 1)
                self.assertEqual(ledger.summary()["outcomes"], ["http_error"])
                self.assertEqual(ledger.summary()["reserved_micro_usd"], 20000)
            finally:
                ledger.close()

    def test_bad_or_unknown_usage_consumes_full_reservation(self):
        for usage in ({}, {"input_tokens": True}, {"input_tokens": mod.TOKEN_CEILING + 1}):
            with self.subTest(usage=usage):
                path = Path(self.temp.name) / (mod.sha(mod.canonical(usage)) + ".sqlite")
                ledger = mod.BudgetLedger(path, self.binding, create=True)
                try:
                    transport = mod.TypeSafeShadowTransport(api_key=CREDENTIAL, account_reference="offline-account", approved=self.approved, ledger=ledger)
                    response = Response({"model": mod.MODEL, "answers": {}, "usage": usage})
                    with patch.object(mod.http.client, "HTTPSConnection") as connection:
                        connection.return_value.getresponse.return_value = response
                        with self.assertRaises(mod.Uncertain):
                            transport.send("routine-extract", "switchyard", self.entries[0]["payload"])
                    self.assertEqual(ledger.summary()["reserved_micro_usd"], 20000)
                    self.assertEqual(ledger.summary()["outcomes"], ["invalid_response"])
                finally:
                    ledger.close()

    def test_crash_after_reservation_is_not_free_headroom(self):
        self.ledger.reserve("switchyard", "routine-extract", b"public")
        other = mod.BudgetLedger(self.path, self.binding)
        try:
            self.assertEqual(other.summary()["outcomes"], ["reserved_unknown_dispatch"])
            with self.assertRaises(mod.Blocked):
                other.reserve("switchyard", "routine-extract", b"public")
        finally:
            other.close()

    def test_combined_24_request_cap_never_resets_on_reopen(self):
        for i in range(24):
            self.ledger.reserve(mod.ARMS[i % 2], str(i), b"public")
        with self.assertRaises(mod.Blocked):
            self.ledger.reserve("switchyard", "twenty-five", b"public")
        other = mod.BudgetLedger(self.path, self.binding)
        try:
            self.assertEqual(other.summary()["reserved_requests"], 24)
            self.assertEqual(other.summary()["reserved_micro_usd"], 480000)
            with self.assertRaises(mod.Blocked):
                other.reserve("jev-skills", "new", b"public")
        finally:
            other.close()

    def test_spend_cap_is_checked_before_dispatch(self):
        ledger = mod.BudgetLedger(Path(self.temp.name) / "smaller.sqlite", "lower-bound", create=True, max_micro_usd=30000)
        try:
            ledger.reserve("switchyard", "one", b"public")
            with self.assertRaises(mod.Blocked):
                ledger.reserve("jev-skills", "two", b"public")
            self.assertEqual(ledger.summary()["reserved_micro_usd"], 20000)
        finally:
            ledger.close()

    def test_concurrent_arms_share_one_atomic_budget(self):
        def reserve(i):
            ledger = mod.BudgetLedger(self.path, self.binding)
            try:
                try:
                    ledger.reserve(mod.ARMS[i % 2], str(i), b"public")
                    return True
                except mod.Blocked:
                    return False
            finally:
                ledger.close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(reserve, range(30))), 24)
        self.assertEqual(self.ledger.summary()["reserved_requests"], 24)

    def test_missing_existing_ledger_and_reinitialization_fail_closed(self):
        with self.assertRaises(sqlite3.OperationalError):
            mod.BudgetLedger(Path(self.temp.name) / "missing.sqlite", self.binding)
        with self.assertRaises(FileExistsError):
            mod.BudgetLedger(self.path, self.binding, create=True)
        with self.assertRaises(mod.Blocked):
            mod.BudgetLedger(self.path, "different-binding")
        for args in ({"max_requests": 25}, {"max_micro_usd": 500001}):
            with self.assertRaises(mod.Blocked):
                mod.BudgetLedger(self.path, self.binding, **args)

    def test_both_native_transport_signatures_use_same_accounting(self):
        with patch.object(mod.http.client, "HTTPSConnection") as connection:
            connection.return_value.getresponse.return_value = Response()
            self.transport.switchyard("routine-extract")(self.entries[0]["payload"])
            self.transport.jev_skills("routine-extract", error_type=RuntimeError)(mod.canonical(self.entries[1]["payload"]), {"Untrusted": "ignored"}, 4)
            self.assertEqual(connection.call_count, 2)
            for call in connection.return_value.request.call_args_list:
                self.assertNotIn("Untrusted", call.kwargs["headers"])
        self.assertEqual(self.ledger.summary()["reserved_requests"], 2)

    def test_preflight_does_not_promote_cached_names_or_public_pricing(self):
        report = preflight.assess({"classifier_provider": "typesafe", "classifier_model": mod.MODEL,
            "credential_present": True, "model_provider": "same-provider", "available_candidate_ids_cached": ["small", "large"]})
        self.assertEqual(report["status"], "prepared_live_blocked")
        self.assertEqual(len(report["missing_prerequisites"]), 4)
        self.assertEqual(report["real_jev_requests"], 0)
        self.assertFalse(report["promotion_authorized"])

    def test_failed_accounting_write_keeps_original_reservation(self):
        with patch.object(mod.http.client, "HTTPSConnection") as connection:
            connection.return_value.getresponse.return_value = Response()
            with patch.object(self.ledger, "finish", side_effect=sqlite3.OperationalError("fixture disk failure")):
                with self.assertRaises(sqlite3.OperationalError):
                    self.transport.send("routine-extract", "switchyard", self.entries[0]["payload"])
        self.assertEqual(self.ledger.summary()["outcomes"], ["reserved_unknown_dispatch"])
        self.assertEqual(self.ledger.summary()["reserved_micro_usd"], 20000)
        with self.assertRaises(mod.Blocked):
            self.ledger.reserve("switchyard", "routine-extract", b"public")

    def test_jev_errors_are_typed_and_nonretryable_after_uncertain_dispatch(self):
        class NativeError(RuntimeError):
            def __init__(self, code, message):
                self.code = code
                super().__init__(message)
        callback = self.transport.jev_skills("routine-extract", error_type=NativeError)
        with patch.object(mod.http.client, "HTTPSConnection", side_effect=TimeoutError) as connection:
            for attempt in range(2):
                with self.assertRaises(NativeError) as raised:
                    callback(mod.canonical(self.entries[1]["payload"]), {}, 4)
                self.assertEqual(raised.exception.code, "shadow_no_retry")
            self.assertEqual(connection.call_count, 1)
        self.assertEqual(self.ledger.summary()["reserved_requests"], 1)

    def test_authorized_credential_can_authenticate_on_first_reserved_request(self):
        bootstrap = proof(account_verified=False, credential_reuse_authorized=True,
                          authenticate_on_first_reserved_request=True)
        mod.validate_prerequisites(bootstrap)
        for changes in ({"credential_reuse_authorized": False}, {"authenticate_on_first_reserved_request": False},
                        {"model_allowlist_verified": False}, {"public_payloads_verified": False}, {"price_ceiling_verified": False}):
            with self.subTest(changes=changes), self.assertRaises(mod.Blocked):
                mod.validate_prerequisites({**bootstrap, **changes})
        inventory = {"classifier_provider": "typesafe", "classifier_model": mod.MODEL,
                     "credential_present": True, "account_verified": False,
                     "credential_reuse_authorized": True, "authenticate_on_first_reserved_request": True,
                     "approved_candidate_ids": []}
        report = preflight.assess(inventory)
        self.assertNotIn("authenticated_typesafe_account_and_exact_model_not_verified", report["missing_prerequisites"])
        self.assertEqual(report["authentication_status"], "pending_first_reserved_request")
        self.assertEqual(report["status"], "prepared_live_blocked")

    def test_all_connection_and_response_failure_phases_have_one_attempt(self):
        failures = (("constructor", ConnectionError), ("request", BrokenPipeError),
                    ("getresponse", ConnectionResetError), ("read", TimeoutError), ("close", OSError))
        for index, (phase, error) in enumerate(failures):
            case_id = tuple(CASES)[index]
            approved = mod.ApprovedPayloads(CASES, [entry(case_id)], proof())
            ledger = mod.BudgetLedger(Path(self.temp.name) / f"phase-{phase}.sqlite", approved.binding + ":offline-account", create=True)
            try:
                broker = mod.TypeSafeShadowTransport(api_key=CREDENTIAL, account_reference="offline-account", approved=approved, ledger=ledger)
                with patch.object(mod.http.client, "HTTPSConnection") as connection:
                    response = Response()
                    connection.return_value.getresponse.return_value = response
                    if phase == "constructor":
                        connection.side_effect = error
                    elif phase == "read":
                        response.read = Mock(side_effect=error)
                    else:
                        getattr(connection.return_value, phase).side_effect = error
                    with self.assertRaises((mod.Uncertain, OSError)):
                        broker.send(case_id, "switchyard", entry(case_id)["payload"])
                    with self.assertRaises(mod.Blocked):
                        broker.send(case_id, "switchyard", entry(case_id)["payload"])
                    self.assertEqual(connection.call_count, 1)
                    self.assertLessEqual(connection.return_value.request.call_count, 1)
                self.assertEqual(ledger.summary()["reserved_requests"], 1)
                self.assertEqual(ledger.summary()["reserved_micro_usd"], 20000)
            finally:
                ledger.close()

    def test_invalid_transport_timeout_cannot_consume_or_dispatch(self):
        for timeout in (True, 0, -1, float("inf"), 16):
            with self.subTest(timeout=timeout), patch.object(mod.http.client, "HTTPSConnection") as connection:
                with self.assertRaises(mod.Blocked):
                    self.transport.send("routine-extract", "switchyard", self.entries[0]["payload"], timeout=timeout)
                connection.assert_not_called()
        self.assertEqual(self.ledger.summary()["reserved_requests"], 0)


if __name__ == "__main__":
    unittest.main()
