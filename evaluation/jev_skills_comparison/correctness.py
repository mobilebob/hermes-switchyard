"""Read-only scorer for separately collected, public synthetic model responses.

This module does not import a model client, load Hermes, send provider requests,
or reuse the Stage A decision ledger. An observation's claim that it came from
an actual provider is not independent proof of that request.
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

MODES = ("fixture", "observed_direct")
STATUSES = ("completed", "failed", "uncertain")
APPROVED_MODELS = ("openai-codex:gpt-6.1-sol", "openai-codex:gpt-6-luna")


class InvalidEvidence(ValueError):
    """Evidence cannot be scored as a valid paired study."""


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_plan(path: Path) -> tuple[dict, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise InvalidEvidence(f"Could not read task plan: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise InvalidEvidence("Expected schema_version 1 plan object")
    if raw.get("study") != "public-synthetic-downstream-model-correctness":
        raise InvalidEvidence("Wrong study identity")
    if raw.get("provider") != "openai-codex" or raw.get("models") != list(APPROVED_MODELS):
        raise InvalidEvidence("Only the frozen same-provider Sol/Luna comparison is supported")
    items = raw.get("cases")
    if not isinstance(items, list) or len(items) != 24:
        raise InvalidEvidence("Expected exactly 24 frozen public cases")
    tasks = {}
    counts = Counter()
    for row in items:
        if not isinstance(row, dict):
            raise InvalidEvidence("Case must be an object")
        key = row.get("id")
        if not isinstance(key, str) or not key or key in tasks or len(key) > 40:
            raise InvalidEvidence("Case IDs must be unique, short strings")
        if row.get("scorer") != "strip_exact":
            raise InvalidEvidence("Only predeclared strip_exact scoring is supported")
        if row.get("category") not in ("extraction", "formatting", "arithmetic", "code_reading"):
            raise InvalidEvidence("Unknown category")
        for field in ("prompt", "expected"):
            if not isinstance(row.get(field), str) or not row[field] or len(row[field]) > 2000:
                raise InvalidEvidence(f"Invalid {field} in {key}")
        tasks[key] = row
        counts[row["category"]] += 1
    if any(counts[category] != 6 for category in ("extraction", "formatting", "arithmetic", "code_reading")):
        raise InvalidEvidence("Expected six cases per category")
    return tasks, digest(canonical(raw))


def read_observations(path: Path, tasks: dict, plan_sha: str) -> dict[tuple[str, str], dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise InvalidEvidence(f"Could not read observations: {exc}") from exc
    rows = {}
    attempt_ids = set()
    mode_seen = set()
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        except ValueError as exc:
            raise InvalidEvidence(f"Line {line_no}: invalid JSON") from exc
        if not isinstance(row, dict):
            raise InvalidEvidence(f"Line {line_no}: expected an object")
        key = (row.get("case_id"), row.get("model_id"))
        if not isinstance(key[0], str) or key[0] not in tasks or key[1] not in APPROVED_MODELS:
            raise InvalidEvidence(f"Line {line_no}: unapproved case or model")
        if key in rows:
            raise InvalidEvidence(f"Line {line_no}: duplicate case/model pair")
        attempt = row.get("attempt_id")
        if not isinstance(attempt, str) or not attempt.strip() or len(attempt) > 128 or attempt in attempt_ids:
            raise InvalidEvidence(f"Line {line_no}: invalid or repeated attempt identity")
        attempt_ids.add(attempt)
        if row.get("plan_sha256") != plan_sha:
            raise InvalidEvidence(f"Line {line_no}: task plan digest mismatch")
        if row.get("prompt_sha256") != digest(tasks[key[0]]["prompt"].encode("utf-8")):
            raise InvalidEvidence(f"Line {line_no}: prompt digest mismatch")
        if row.get("routing_applied") is not False:
            raise InvalidEvidence(f"Line {line_no}: automatic routing may not be applied")
        mode = row.get("evidence_mode")
        status = row.get("status")
        if mode not in MODES or status not in STATUSES:
            raise InvalidEvidence(f"Line {line_no}: unknown evidence mode or request status")
        mode_seen.add(mode)
        if status == "completed":
            if not isinstance(row.get("answer"), str) or len(row["answer"]) > 20000:
                raise InvalidEvidence(f"Line {line_no}: completed request needs bounded answer")
            if type(row.get("elapsed_ms")) not in (int, float) or not math.isfinite(row["elapsed_ms"]) or row["elapsed_ms"] < 0:
                raise InvalidEvidence(f"Line {line_no}: completed request needs measured latency")
            for field in ("input_tokens", "output_tokens"):
                if type(row.get(field)) is not int or row[field] < 0:
                    raise InvalidEvidence(f"Line {line_no}: completed request needs actual usage fields")
        else:
            if row.get("answer") is not None:
                raise InvalidEvidence(f"Line {line_no}: failure/uncertainty cannot claim an answer")
            for field in ("input_tokens", "output_tokens"):
                if row.get(field) is not None and (type(row[field]) is not int or row[field] < 0):
                    raise InvalidEvidence(f"Line {line_no}: usage must be known nonnegative integer or unknown")
            elapsed = row.get("elapsed_ms")
            if elapsed is not None and (type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0):
                raise InvalidEvidence(f"Line {line_no}: invalid elapsed time")
        rows[key] = row
    if len(mode_seen) > 1:
        raise InvalidEvidence("Fixture and observed-direct evidence cannot be mixed")
    return rows


def nearest_rank_p95(numbers: list[float]) -> float | None:
    if not numbers:
        return None
    values = sorted(numbers)
    return values[math.ceil(.95 * len(values)) - 1]


def evaluate(tasks: dict, rows: dict[tuple[str, str], dict], plan_sha: str) -> dict:
    mode = next((r["evidence_mode"] for r in rows.values()), None)
    by_model = {}
    for model in APPROVED_MODELS:
        observed = [r for (case_id, name), r in rows.items() if name == model]
        complete = [r for r in observed if r["status"] == "completed"]
        correctness = {category: {"correct": 0, "completed": 0, "expected": 6} for category in
                       ("extraction", "formatting", "arithmetic", "code_reading")}
        for (case_id, name), row in rows.items():
            if name == model and row["status"] == "completed":
                category = tasks[case_id]["category"]
                correctness[category]["completed"] += 1
                correctness[category]["correct"] += int(row["answer"].strip() == tasks[case_id]["expected"])
        times = [r["elapsed_ms"] for r in complete]
        by_model[model] = {
            "observed": len(observed),
            "missing": sorted(set(tasks) - {case_id for case_id, name in rows if name == model}),
            "completed": len(complete),
            "failed": sum(r["status"] == "failed" for r in observed),
            "uncertain": sum(r["status"] == "uncertain" for r in observed),
            "correct": sum(x["correct"] for x in correctness.values()),
            "by_category": correctness,
            "latency_ms_median_completed": statistics.median(times) if times else None,
            "latency_ms_p95_completed": nearest_rank_p95(times),
            "known_input_tokens_completed": sum(r["input_tokens"] for r in complete),
            "known_output_tokens_completed": sum(r["output_tokens"] for r in complete),
        }
    pairs = Counter()
    for case_id, case in tasks.items():
        a, b = (rows.get((case_id, model)) for model in APPROVED_MODELS)
        if a is None or b is None or a["status"] != "completed" or b["status"] != "completed":
            pairs["not_scored"] += 1
            continue
        ac = a["answer"].strip() == case["expected"]
        bc = b["answer"].strip() == case["expected"]
        pairs["both_correct" if ac and bc else "sol_only_correct" if ac else
              "luna_only_correct" if bc else "both_wrong"] += 1
    complete = all(r["observed"] == len(tasks) and r["completed"] == len(tasks) for r in by_model.values())
    return {
        "schema_version": 1,
        "study": "public-synthetic-downstream-model-correctness",
        "plan_sha256": plan_sha,
        "evidence_mode": mode,
        "status": "incomplete_or_unresolved" if not complete else
                  "complete_fixture_only" if mode == "fixture" else "complete_observed_direct_unverified",
        "expected_model_calls": 2 * len(tasks),
        "rows_received": len(rows),
        "models": by_model,
        "paired_outcomes": {k: pairs[k] for k in ("both_correct", "sol_only_correct", "luna_only_correct", "both_wrong", "not_scored")},
        "reported_cost_usd": None,
        "invoice_confirmed": False,
        "provider_calls_independently_verified": False,
        "routing_quality_measured": False,
        "realized_savings_measured": False,
        "promotion_authorized": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=Path(__file__).with_name("heldout_cases.json"))
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--preflight", action="store_true", help="Print frozen plan identity without reading any model results")
    args = parser.parse_args(argv)
    if args.preflight and args.observations is not None:
        parser.error("--preflight and --observations are mutually exclusive")
    if not args.preflight and args.observations is None:
        parser.error("--observations is required unless --preflight is used")
    try:
        tasks, plan_sha = load_plan(args.plan)
        if args.preflight:
            print(json.dumps({"study": "public-synthetic-downstream-model-correctness", "plan_sha256": plan_sha,
                              "cases": len(tasks), "models": list(APPROVED_MODELS),
                              "planned_direct_model_calls": 2 * len(tasks),
                              "provider_requests_executed": 0, "dispatch_available": False}, indent=2))
            return 0
        result = evaluate(tasks, read_observations(args.observations, tasks, plan_sha), plan_sha)
    except InvalidEvidence as exc:
        print(f"Invalid study evidence: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"].startswith("complete_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
