"""Offline, non-executing comparison of reported shadow decisions.

Consumes operator-supplied lab observations; never imports plugins, contacts a
model provider, reads Hermes state, or changes routing behavior.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ARMS = ("switchyard", "jev-skills")
EGRESS = ("none", "coarse_features", "redacted_text")


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
        rows[key] = row
    return rows


def compare(cases: dict[str, dict], rows: dict[tuple[str, str], dict]) -> dict:
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
            "source_shas": dict(sorted(Counter(r["source_sha"] for r in observed).items())),
        }
    ready = all(
        not r["missing"] and not r["policy_violations"] and not r["privacy_violations"]
        for r in reports.values()
    )
    return {
        "schema_version": 1,
        "status": "complete_synthetic_shadow_screen" if ready else "incomplete_or_blocked",
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
    return 0 if report["status"] == "complete_synthetic_shadow_screen" else 1


if __name__ == "__main__":
    raise SystemExit(main())
