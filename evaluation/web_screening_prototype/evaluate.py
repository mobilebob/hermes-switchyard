"""Six-case experiment; --live sends at most six requests with the existing client."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hermes_switchyard.client import DecisionClient, TYPESAFE_ENDPOINT
from hermes_switchyard.web_screening_prototype import screen_result

CASES = Path(__file__).with_name("cases.json")


def load_cases():
    cases = json.loads(CASES.read_text())["cases"]
    if len(cases) != 6 or len({c["id"] for c in cases}) != 6:
        raise ValueError("Expected exactly six distinct synthetic cases")
    return cases


def summarize(rows, total=6):
    observed = [r for r in rows if r["observation"]["reason"] == "observed"]
    accuracy = {}
    for category in ("relevance", "injection", "sufficiency"):
        correct = sum(
            r["observation"]["judgments"][category] == r["expected"][category]
            for r in observed
        )
        accuracy[category] = {
            "correct": correct,
            "judged": len(observed),
            "accuracy": correct / len(observed) if observed else None,
            "unjudged": total - len(observed),
        }
    usage = {}
    for r in observed:
        for key, value in r["observation"].get("usage", {}).items():
            if type(value) in (int, float):
                usage[key] = usage.get(key, 0) + value
    latencies = [r["observation"]["elapsed_ms"] for r in observed]
    return {
        "completed": len(observed),
        "attempted": sum(r["observation"]["request_count"] for r in rows),
        "blocked": total - len(rows),
        "failed_or_skipped": len(rows) - len(observed),
        "accuracy": accuracy,
        "observed_usage": usage or None,
        "usage_scope": "Validated responses only; usage of uncertain requests is unknown, not zero.",
        "latency_ms": {
            "median": statistics.median(latencies),
            "minimum": min(latencies),
            "maximum": max(latencies),
        }
        if latencies
        else None,
        "label_comparison_only": True,
        "hermes_answer_improvement_measured": False,
    }


def evaluate(client, cases, output=None):
    rows = []
    report = {"cases": rows, "summary": summarize(rows, len(cases))}
    for case in cases:
        observation = screen_result(
            client,
            case["question"],
            case["result"],
            enabled=True,
            public_source=True,
            public_or_sanitized_data_ack=True,
        )
        rows.append(
            {"id": case["id"], "expected": case["expected"], "observation": observation}
        )
        report = {
            "scope": "Six synthetic documents, not a live-web accuracy benchmark",
            "model_requested": client.model,
            "cases_sha256": hashlib.sha256(CASES.read_bytes()).hexdigest(),
            "cases": rows,
            "summary": summarize(rows, len(cases)),
        }
        if output is not None:
            output.write_text(json.dumps(report, indent=2) + "\n")
        if observation["reason"] != "observed":
            break  # Never retry or continue after an uncertain/error response.
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error(
            "No requests made. Run offline unit tests first; --live explicitly enables this experiment."
        )
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        parser.error(
            "Existing TypeSafe configuration must supply TYPESAFE_API_KEY; no requests made."
        )
    cases = load_cases()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Ordinary output-file exclusivity, not an authorization or billing ledger.
    with args.output.open("x") as f:
        f.write('{"status":"started","cases":[]}\n')
    client = DecisionClient(
        api_key=key, endpoint=TYPESAFE_ENDPOINT, model="jev-1.14.0", timeout=30
    )
    report = evaluate(client, cases, args.output)
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
