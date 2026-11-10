"""Isolated decision-only live trial over exactly reviewed native request bodies.

Prepare verifies saved first-party evidence and hashes; execute has no retries,
no fallback, no main-model calls and no model-application path. Use one durable
output directory for the entire owner authorization; never reset its ledger.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time

from billing_evidence import verify_billing
from collect import verify_source, worker_env
from compare import read_cases, compare, PINS
from live_transport import ApprovedPayloads, BudgetLedger, TypeSafeShadowTransport, Blocked, Uncertain, canonical, sha, body_for
from live_worker import CATALOG

HERE = Path(__file__).resolve().parent
RUNNER_FILES = ("live_driver.py", "live_worker.py", "worker.py", "live_transport.py", "billing_evidence.py", "collect.py", "compare.py", "cases.json")


def runner_hashes():
    return {name: sha((HERE/name).read_bytes()) for name in RUNNER_FILES}


def verify_plan(plan: dict, cases: dict) -> list:
    if plan.get("catalog") != CATALOG or plan.get("real_provider_requests") != 0:
        raise Blocked("not_a_reviewed_offline_native_plan")
    expected = {(key, arm) for key, c in cases.items() if c["egress_limit"] == "redacted_text" for arm in PINS}
    found = set()
    entries = plan.get("entries", [])
    for entry in entries:
        key = (entry.get("case_id"), entry.get("arm"))
        if key not in expected or key in found:
            raise Blocked("invalid_reviewed_case_arm_coverage")
        found.add(key)
        case = cases[key[0]]
        body = body_for(entry["payload"])
        field = "task" if key[1] == "switchyard" else "user_turn"
        if (entry.get("source_sha") != PINS[key[1]] or entry.get("task_sha256") != sha(case["task"].encode())
                or entry.get("body_sha256") != sha(body) or entry["payload"]["state"].get(field) != case["task"]):
            raise Blocked("reviewed_body_identity_mismatch")
    if found != expected:
        raise Blocked("incomplete_reviewed_native_plan")
    return entries


def prepare(output: Path, reviewed: Path, evidence: Path, roots: dict, credential: str):
    if not credential:
        raise Blocked("missing_existing_typesafe_credential")
    cases = read_cases(HERE/"cases.json")
    plan_raw = reviewed.read_bytes()
    plan = json.loads(plan_raw)
    entries = verify_plan(plan, cases)
    billing = verify_billing(evidence)
    sources = {arm: verify_source(path, arm) for arm, path in roots.items()}
    proof = {"provider": "typesafe", "model": "jev-1.14.0", "account_verified": False,
        "credential_reuse_authorized": True, "authenticate_on_first_reserved_request": True,
        "account_reference": "owner-approved-existing-typesafe-credential", "credential_sha256": sha(credential.encode()),
        "model_allowlist_verified": True, "allowlist_reference": "owner-approved-sol-luna-existing-codex-account",
        "public_payloads_verified": True, "payload_review_reference": sha(plan_raw),
        "price_ceiling_verified": True, "pricing_reference": sha(canonical(billing)),
        "rate_micro_usd_per_million": 42000, "output_free": True, "no_other_fees": True,
        "billable_input_token_ceiling": 262144}
    approved = ApprovedPayloads(cases, entries, proof)
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    os.chmod(output, 0o700)
    manifest = {"authorization": "stage-a-24-050-no-retries-v1", "source_revisions": sources,
        "source_roots": {arm: str(path.resolve()) for arm, path in roots.items()},
        "runner_hashes": runner_hashes(), "catalog": CATALOG, "billing": billing,
        "reviewed_plan_sha256": sha(plan_raw), "billing_directory": str(evidence.resolve()),
        "approved_binding": approved.binding, "max_external_requests": 24, "max_micro_usd": 500000,
        "main_model_calls_authorized": 0, "model_changes_authorized": False, "retries": 0}
    for name, value in (("manifest.json", manifest), ("proof.json", proof), ("reviewed-plan.json", plan)):
        (output/name).write_text(json.dumps(value, indent=2)+"\n")
    return manifest


def native_decision(payload: dict, handler, workspace: Path):
    with tempfile.TemporaryDirectory(prefix="worker-", dir=workspace) as temp:
        home = Path(temp)
        proc = subprocess.Popen([sys.executable, "-B", str(HERE/"live_worker.py")], cwd=home,
            env=worker_env(home), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        try:
            proc.stdin.write(json.dumps(payload)+"\n")
            proc.stdin.flush()
            requests = 0
            while True:
                if not selector.select(timeout=45):
                    raise Blocked("native_worker_deadline")
                line = proc.stdout.readline(100000)
                if not line:
                    raise Blocked("native_worker_missing_evidence")
                event = json.loads(line)
                if event.get("kind") == "provider_request":
                    requests += 1
                    if requests > 1:
                        raise Blocked("native_retry_forbidden")
                    try:
                        response = handler(event["payload"])
                        reply = {"response": response}
                    except (Blocked, Uncertain):
                        reply = {"error": "parent_failed_no_retry"}
                    proc.stdin.write(json.dumps(reply)+"\n")
                    proc.stdin.flush()
                elif event.get("kind") == "decision":
                    proc.wait(timeout=10)
                    return event
                else:
                    raise Blocked("invalid_native_event")
        finally:
            selector.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()


def summarize(cases, rows, ledger):
    result = compare(cases, {(r["case_id"], r["arm"]): r for r in rows})
    ready = (result["status"] == "complete_synthetic_shadow_screen" and
             not ledger.summary()["unresolved_or_failed_requests"] and
             all(r["evidence_kind"] in ("live_jev_decision", "local_privacy_exclusion") for r in rows))
    result["status"] = "complete_live_decision_only_shadow_screen" if ready else "blocked_live_decision_only_shadow_screen"
    result["scope"] = "Live Jev decision-only policy screen; local exclusions explicit; not downstream correctness or realized savings"
    result["ledger"] = ledger.summary()
    result["main_model_evaluation_requests"] = 0
    result["applied_model_changes"] = 0
    for arm, info in result["arms"].items():
        own = [r for r in rows if r["arm"] == arm]
        info["native_decisions"] = sum(r["evidence_kind"] == "live_jev_decision" for r in own)
        info["local_privacy_exclusions"] = sum(r["evidence_kind"] == "local_privacy_exclusion" for r in own)
        info["request_accounting"] = [r["ledger_invocation"] for r in own if r.get("ledger_invocation")]
    return result


def execute(output: Path, credential: str, initialize=False):
    manifest = json.loads((output/"manifest.json").read_text())
    state_hashes = {name: sha((output/name).read_bytes()) for name in ("manifest.json", "proof.json", "reviewed-plan.json")}
    if runner_hashes() != manifest["runner_hashes"]:
        raise Blocked("driver_sources_changed_since_review")
    billing = verify_billing(Path(manifest["billing_directory"]))
    if billing != manifest["billing"]:
        raise Blocked("billing_evidence_changed")
    proof = json.loads((output/"proof.json").read_text())
    cases = read_cases(HERE/"cases.json")
    plan = json.loads((output/"reviewed-plan.json").read_text())
    entries = verify_plan(plan, cases)
    approved = ApprovedPayloads(cases, entries, proof)
    if approved.binding != manifest["approved_binding"]:
        raise Blocked("approval_binding_changed")
    roots = {a: Path(p) for a, p in manifest["source_roots"].items()}
    for arm in PINS:
        if verify_source(roots[arm], arm) != manifest["source_revisions"][arm]:
            raise Blocked("source_revision_changed")
    ledger = BudgetLedger(output/"budget.sqlite", approved.binding+":"+proof["account_reference"], create=initialize)
    try:
        broker = TypeSafeShadowTransport(api_key=credential, account_reference=proof["account_reference"], approved=approved, ledger=ledger)
        path = output/"observations.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        done = {(r["case_id"], r["arm"]) for r in rows}
        if len(done) != len(rows):
            raise Blocked("duplicate_observation_evidence")
        prior = {(a, c): i for i, a, c in ledger.connection.execute("SELECT invocation,arm,case_id FROM attempts")}
        stop = bool(ledger.summary()["unresolved_or_failed_requests"])
        (output/"responses").mkdir(exist_ok=True, mode=0o700)
        with path.open("a", encoding="utf-8") as stream:
            for case_id, case in cases.items():
                for arm in PINS:
                    key = (case_id, arm)
                    if key in done:
                        continue
                    started = time.monotonic_ns()
                    row = {"case_id": case_id, "arm": arm, "source_sha": PINS[arm], "mode": "shadow", "applied": False,
                        "would_switch": False, "proposed_model": None, "abstained": True, "external_calls": 0,
                        "egress": "none", "latency_ms": 0.0, "worker_failed": False, "ledger_invocation": None,
                        "evidence_kind": "not_attempted_after_stop", "abstention_reason": "trial_stopped"}
                    if case["egress_limit"] != "redacted_text":
                        row.update(evidence_kind="local_privacy_exclusion", abstention_reason="case_forbids_text_egress")
                    elif (arm, case_id) in prior:
                        row.update(evidence_kind="prior_attempt_not_replayed", external_calls=1, egress="redacted_text",
                                   ledger_invocation=prior[(arm, case_id)], abstention_reason="prior_attempt_never_replayed")
                        stop = True
                    elif not stop:
                        failed = []
                        def handler(body):
                            try:
                                if (runner_hashes() != manifest["runner_hashes"] or
                                        verify_billing(Path(manifest["billing_directory"])) != billing or
                                        any(sha((output/name).read_bytes()) != digest for name, digest in state_hashes.items())):
                                    raise Blocked("prerequisite_changed_before_dispatch")
                                if any(verify_source(roots[a], a) != manifest["source_revisions"][a] for a in PINS):
                                    raise Blocked("source_changed_before_dispatch")
                                result = broker.send(case_id, arm, body, timeout=15)
                                if credential.encode() in canonical(result):
                                    raise Blocked("credential_in_response_not_recorded")
                                (output/"responses"/(arm+"--"+case_id+".json")).write_bytes(canonical(result))
                                return result
                            except (Blocked, Uncertain):
                                failed.append(True)
                                raise
                        payload = {"arm": arm, "source_root": str(roots[arm]), "case": {"id": case_id, "task": case["task"]}, "catalog": CATALOG}
                        try:
                            decision = native_decision(payload, handler, output)
                            if decision["network_attempts"] or decision["process_attempts"]:
                                raise Blocked("worker_escape_attempt")
                            if decision.get("worker_failed"):
                                raise Blocked("native_worker_failed")
                            if decision["task_received"] != case["task"] or decision["candidate_ids"] != [CATALOG["provider"]+":"+m["model"] for m in CATALOG["models"]]:
                                raise Blocked("native_input_parity_failure")
                            proposed = decision["proposed_model"]
                            row.update(evidence_kind="live_jev_decision", proposed_model=proposed, abstained=decision["abstained"],
                                abstention_reason=decision["abstention_reason"], raw_decision=decision["raw_decision"],
                                would_switch=proposed is not None and proposed != CATALOG["current"])
                        except Exception as exc:
                            row.update(evidence_kind="worker_or_broker_failure", worker_failed=True, abstention_reason=type(exc).__name__)
                            failed.append(True)
                        observed = ledger.connection.execute("SELECT invocation FROM attempts WHERE arm=? AND case_id=?", (arm, case_id)).fetchone()
                        if observed:
                            row.update(external_calls=1, egress="redacted_text", ledger_invocation=observed[0])
                        if failed:
                            if row["evidence_kind"] == "live_jev_decision":
                                row["evidence_kind"] = "provider_failure_abstention"
                            stop = True
                    row["latency_ms"] = (time.monotonic_ns()-started)/1_000_000
                    row["safety_decision"] = "rejected" if row["would_switch"] and not case["may_switch"] else "abstained" if row["abstained"] else "allowed_shadow_only"
                    rows.append(row)
                    stream.write(json.dumps(row, sort_keys=True)+"\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                    (output/"comparison.json").write_text(json.dumps(summarize(cases, rows, ledger), indent=2)+"\n")
        for arm in PINS:
            if verify_source(roots[arm], arm) != manifest["source_revisions"][arm]:
                raise Blocked("source_changed_during_trial")
        result = summarize(cases, rows, ledger)
        (output/"comparison.json").write_text(json.dumps(result, indent=2)+"\n")
        return result
    finally:
        ledger.close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--prepare", action="store_true")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--initialize-ledger", action="store_true")
    p.add_argument("--reviewed-plan", type=Path)
    p.add_argument("--billing-evidence", type=Path)
    p.add_argument("--switchyard-source", type=Path)
    p.add_argument("--jev-skills-source", type=Path)
    args = p.parse_args(argv)
    if args.prepare == args.execute:
        p.error("select exactly one of --prepare or --execute")
    try:
        credential = os.environ.get("TYPESAFE_API_KEY", "")
        if args.prepare:
            if not all((args.reviewed_plan, args.billing_evidence, args.switchyard_source, args.jev_skills_source)):
                p.error("prepare needs reviewed plan, billing evidence and both source checkouts")
            prepare(args.output, args.reviewed_plan, args.billing_evidence,
                {"switchyard": args.switchyard_source, "jev-skills": args.jev_skills_source}, credential)
            result = {"status": "prepared_verified_no_dispatch", "real_requests": 0}
        else:
            result = execute(args.output, credential, args.initialize_ledger)
    except Exception as exc:
        print(json.dumps({"status": "blocked", "reason": type(exc).__name__}))
        return 2
    print(json.dumps(result, indent=2))
    return 1 if result.get("status") == "blocked_live_decision_only_shadow_screen" else 0


if __name__ == "__main__":
    raise SystemExit(main())
