"""Offline, non-executing comparison of reported shadow decisions.

Consumes operator-supplied lab observations; never imports plugins, contacts a
model provider, reads Hermes state, or changes routing behavior.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path

ARMS = ("switchyard", "jev-skills")
EGRESS = ("none", "coarse_features", "redacted_text")
PINS = {
    "switchyard": "cc45394c6c04627f6e62736f987fceb84117a6d9",
    "jev-skills": "8316358bf60e98a21477582af829ef11c9cf52fb",
}


def validate_collected(row: dict, case: dict) -> None:
    """Reject mislabeled fixtures, changed task/catalog identity and false accounting."""
    catalog = json.loads(Path(__file__).with_name("catalog.json").read_text(encoding="utf-8"))
    catalog_hash = hashlib.sha256(json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    refs = [f"fixture:{m['model']}" for m in catalog["models"]]
    task_hash = hashlib.sha256(case["task"].encode()).hexdigest()
    if row.get("source_sha") != PINS[row["arm"]]:
        raise InvalidEvidence("Collector source revision differs from pinned source")
    if (row.get("catalog_sha256") != catalog_hash or row.get("task_sha256") != task_hash
            or row.get("approved_candidates") != refs or row.get("current_model") != catalog["current"]):
        raise InvalidEvidence("Collector task or approved candidate identity mismatch")
    if row.get("evidence_kind") not in ("offline_fixture", "offline_no_response"):
        raise InvalidEvidence("Collector accepts offline evidence only")
    if row.get("provider_requests") != [] or row["external_calls"] != 0 or row["egress"] != "none":
        raise InvalidEvidence("Offline collection cannot report provider calls or egress")
    if type(row.get("simulated_requests")) is not int or not 0 <= row["simulated_requests"] <= 20:
        raise InvalidEvidence("Invalid simulated request count")
    proposed = row.get("proposed_model")
    if proposed is not None and proposed not in refs:
        raise InvalidEvidence("Proposal outside approved fixture candidates")
    if row["would_switch"] != (proposed is not None and proposed != catalog["current"]):
        raise InvalidEvidence("Proposal and would_switch disagree")
    if type(row.get("abstained")) is not bool or type(row.get("worker_failed")) is not bool:
        raise InvalidEvidence("Collector requires explicit abstention and failure evidence")
    if row.get("safety_decision") not in ("rejected", "abstained", "allowed_shadow_only"):
        raise InvalidEvidence("Missing collector safety decision")
    expected_safety = ("rejected" if row["would_switch"] and not case["may_switch"] else
                       "abstained" if row["abstained"] else "allowed_shadow_only")
    if row["safety_decision"] != expected_safety:
        raise InvalidEvidence("Safety decision contradicts policy or abstention")
    counters = (row.get("network_attempts"), row.get("process_attempts"))
    accounting = row.get("attempt_accounting")
    if row["worker_failed"] and accounting == "unknown_worker_failed":
        if any(c is not None for c in counters):
            raise InvalidEvidence("Unknown worker accounting cannot claim observed attempt counts")
    elif accounting != "observed_python_guards" or any(type(c) is not int or c < 0 for c in counters):
        raise InvalidEvidence("Missing or invalid observed escape-attempt accounting")
    elif not row["worker_failed"] and any(counters):
        raise InvalidEvidence("Successful offline worker cannot have escape attempts")


def require_frozen_cases(cases: dict) -> None:
    raw = json.loads(Path(__file__).with_name("cases.json").read_text(encoding="utf-8"))
    if cases != {case["id"]: case for case in raw["cases"]}:
        raise InvalidEvidence("Collector evidence requires the complete frozen case set and policy")


class InvalidEvidence(ValueError):
    """The supplied observations cannot be treated as comparable evidence."""


def read_cases(path: Path) -> dict[str, dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise InvalidEvidence(f"Could not read cases: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1 or not isinstance(raw.get("cases"), list):
        raise InvalidEvidence("Cases require schema_version=1 and cases array")
    cases = {}
    for case in raw["cases"]:
        if not isinstance(case, dict):
            raise InvalidEvidence("Each case must be an object")
        key = case.get("id")
        if not isinstance(key, str) or not key or key in cases:
            raise InvalidEvidence("Case IDs must be unique, nonempty strings")
        if type(case.get("may_switch")) is not bool or case.get("egress_limit") not in EGRESS:
            raise InvalidEvidence(f"Invalid routing/privacy policy for {key}")
        if not isinstance(case.get("task"), str) or not case["task"].strip():
            raise InvalidEvidence(f"Missing task for {key}")
        cases[key] = case
    if not cases:
        raise InvalidEvidence("Case set is empty")
    return cases


def read_observations(path: Path, cases: dict[str, dict]) -> dict[tuple[str, str], dict]:
    rows = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise InvalidEvidence(f"Could not read observations: {exc}") from exc
    for i, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise InvalidEvidence(f"Line {i}: invalid JSON") from exc
        if not isinstance(row, dict):
            raise InvalidEvidence(f"Line {i}: observation is not an object")
        key = (row.get("case_id"), row.get("arm"))
        if not isinstance(key[0], str) or key[0] not in cases or key[1] not in ARMS:
            raise InvalidEvidence(f"Line {i}: unknown case_id or arm")
        if key in rows:
            raise InvalidEvidence(f"Line {i}: duplicate case/arm observation")
        if row.get("mode") != "shadow" or row.get("applied") is not False:
            raise InvalidEvidence(f"Line {i}: only non-applying shadow observations are accepted")
        if type(row.get("would_switch")) is not bool:
            raise InvalidEvidence(f"Line {i}: would_switch must be boolean")
        if row.get("egress") not in EGRESS:
            raise InvalidEvidence(f"Line {i}: egress must be an explicit classification")
        source_sha = row.get("source_sha")
        if not isinstance(source_sha, str) or len(source_sha) != 40 or any(c not in "0123456789abcdef" for c in source_sha):
            raise InvalidEvidence(f"Line {i}: source_sha must be an exact lowercase SHA-1")
        if type(row.get("latency_ms")) not in (int, float) or not 0 <= row["latency_ms"] < 1_000_000:
            raise InvalidEvidence(f"Line {i}: missing/invalid measured latency_ms")
        if type(row.get("external_calls")) is not int or not 0 <= row["external_calls"] <= 20:
            raise InvalidEvidence(f"Line {i}: missing/invalid external_calls")
        if row.get("would_switch") and (not isinstance(row.get("proposed_model"), str) or not row["proposed_model"].strip()):
            raise InvalidEvidence(f"Line {i}: a switch proposal must identify a model")
        if row.get("schema_version", 1) not in (1, 2):
            raise InvalidEvidence(f"Line {i}: unsupported evidence schema")
        if row.get("schema_version", 1) == 2:
            require_frozen_cases(cases)
            validate_collected(row, cases[key[0]])
        elif any(field in row for field in (
            "evidence_kind", "approved_candidates", "task_sha256", "catalog_sha256",
            "current_model", "provider_requests", "simulated_requests", "abstained",
            "safety_decision", "worker_failed", "network_attempts", "process_attempts", "attempt_accounting",
        )):
            raise InvalidEvidence("Collector evidence requires schema_version=2")
        rows[key] = row
    kinds = {(r.get("schema_version", 1), r.get("evidence_kind", "operator_supplied")) for r in rows.values()}
    if len(kinds) > 1:
        raise InvalidEvidence("Do not mix fixture scenarios or operator and collector evidence")
    return rows


def compare(cases: dict[str, dict], rows: dict[tuple[str, str], dict]) -> dict:
    if any(row.get("schema_version") == 2 for row in rows.values()):
        require_frozen_cases(cases)
    reports = {}
    for arm in ARMS:
        observed = [row for (case_id, name), row in rows.items() if name == arm]
        missing = sorted(key for key in cases if (key, arm) not in rows)
        policy_violations = []
        privacy_violations = []
        for (case_id, name), row in rows.items():
            if name != arm:
                continue
            case = cases[case_id]
            if row["would_switch"] and not case["may_switch"]:
                policy_violations.append(case_id)
            if EGRESS.index(row["egress"]) > EGRESS.index(case["egress_limit"]):
                privacy_violations.append(case_id)
        reports[arm] = {
            "observed": len(observed),
            "missing": missing,
            "switch_proposals": sum(r["would_switch"] for r in observed),
            "policy_violations": sorted(policy_violations),
            "privacy_violations": sorted(privacy_violations),
            "external_calls": sum(r["external_calls"] for r in observed),
            "latency_ms_total": sum(r["latency_ms"] for r in observed),
            "latency_ms_p50": statistics.median(r["latency_ms"] for r in observed) if observed else None,
            "latency_ms_p95_nearest_rank": sorted(r["latency_ms"] for r in observed)[math.ceil(.95 * len(observed)) - 1] if observed else None,
            "abstentions": sum(r.get("abstained", False) for r in observed),
            "safety_decisions": dict(sorted(Counter(r.get("safety_decision", "unreported") for r in observed).items())),
            "simulated_requests": sum(r.get("simulated_requests", 0) for r in observed),
            "worker_failures": sorted(r["case_id"] for r in observed if r.get("worker_failed")),
            "source_shas": dict(sorted(Counter(r["source_sha"] for r in observed).items())),
        }
    ready = all(
        not r["missing"] and not r["policy_violations"] and not r["privacy_violations"] and not r["worker_failures"]
        for r in reports.values()
    )
    kinds = sorted({r.get("evidence_kind", "operator_supplied") for r in rows.values()})
    status = "complete_synthetic_shadow_screen" if ready else "incomplete_or_blocked"
    if ready and kinds == ["offline_fixture"]:
        status = "offline_fixture_only"
    if ready and kinds == ["offline_no_response"]:
        status = "offline_no_response_only"
    return {
        "schema_version": 1,
        "status": status,
        "evidence_kinds": kinds,
        "scope": "operator-supplied shadow observations; not downstream task correctness or realized savings",
        "case_count": len(cases),
        "arms": reports,
        "promotion_authorized": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("cases.json"))
    parser.add_argument("--observations", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        cases = read_cases(args.cases)
        report = compare(cases, read_observations(args.observations, cases))
    except InvalidEvidence as exc:
        print(f"Invalid comparison evidence: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] in (
        "complete_synthetic_shadow_screen", "offline_fixture_only", "offline_no_response_only"
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
