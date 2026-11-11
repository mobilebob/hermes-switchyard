"""Collect isolated routing decisions offline; there is deliberately no live mode.

Default invocation is prerequisite discovery only. Offline workers use either an
unavailable classifier or explicitly synthetic classifier answers, never inference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from compare import ARMS, PINS, InvalidEvidence, compare, read_cases, read_observations  # noqa: E402


PREREQUISITES = [
    "Owner-approved Jev request and monetary caps, timeout, retry and unresolved-dispatch policy.",
    "Explicit approved provider/account/model IDs and current model; fixture IDs authorize no real models.",
    "Approved public/synthetic outbound payloads and fixed provider transport with request-intent/usage accounting.",
    "Separate reviewed live transport implementation; this collector cannot dispatch paid requests.",
    "Held-out tasks and objective downstream correctness checks before any value or promotion claim.",
]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_source(root: Path, arm: str) -> dict:
    root = root.resolve(strict=True)
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE).strip()
    if Path(git("rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError(f"{arm}: source root must be repository root")
    sha = git("rev-parse", "HEAD")
    if sha != PINS[arm]:
        raise ValueError(f"{arm}: source revision does not match pin")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError(f"{arm}: source checkout must be clean, including untracked files")
    return {"source_sha": sha, "clean": True, "identity": "isolated checkout, not gateway runtime"}


def load_catalog(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("scope") != "synthetic-offline-only" or value.get("provider") != "fixture":
        raise ValueError("Only synthetic-offline-only fixture catalogs are accepted")
    models = value.get("models")
    if not isinstance(models, list) or len(models) < 2:
        raise ValueError("At least two explicit approved fixture models required")
    refs = []
    ids = []
    for m in models:
        if m.get("approved") is not True:
            raise ValueError("Every fixture candidate requires explicit approval")
        for key in ("id", "model", "description"):
            if not isinstance(m.get(key), str) or not m[key].strip():
                raise ValueError("Missing fixture candidate identity/description")
        if ":" in m["model"] or ":" in m["id"]:
            raise ValueError("Fixture model and ID cannot encode another provider")
        if type(m.get("cost")) not in (int, float) or not 0 <= m["cost"] < 100:
            raise ValueError("Invalid synthetic cost ordering")
        if type(m.get("context_limit")) is not int or m["context_limit"] < 4096:
            raise ValueError("Fixture candidates must hold the common context")
        refs.append(f"fixture:{m['model']}")
        ids.append(m["id"])
    if len(set(refs)) != len(refs) or len(set(ids)) != len(ids) or value.get("current") not in refs:
        raise ValueError("Duplicate candidates or current model absent from approved set")
    if value != json.loads((HERE / "catalog.json").read_text(encoding="utf-8")):
        raise ValueError("Stage A requires the frozen canonical fixture catalog")
    return value


def worker_env(home: Path) -> dict:
    # Allowlist, rather than attempting to enumerate all possible credential names.
    return {
        "PATH": os.defpath, "HOME": str(home), "HERMES_HOME": str(home / ".hermes"),
        "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
        "TMPDIR": str(home), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
        "HERMES_PROFILE": "default", "LC_ALL": "C.UTF-8",
    }


def safety(case: dict, proposed, catalog: dict, abstained: bool) -> tuple[str, str | None]:
    refs = {f"fixture:{m['model']}" for m in catalog["models"]}
    if proposed is not None and proposed not in refs:
        return "rejected", "unapproved_or_cross_provider_model"
    if proposed and proposed != catalog["current"] and not case["may_switch"]:
        return "rejected", "case_policy_forbids_switch"
    if abstained:
        return "abstained", "implementation_abstained"
    return "allowed_shadow_only", None


def collect(roots: dict, output: Path, scenario: str, catalog_path: Path) -> dict:
    if scenario not in ("no-response", "synthetic-simple"):
        raise ValueError("No live mode or paid transport exists")
    cases = read_cases(HERE / "cases.json")
    catalog = load_catalog(catalog_path)
    sources = {arm: verify_source(roots[arm], arm) for arm in ARMS}
    # Exclusive output directory prevents overwriting or mixing experiments.
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 2, "scenario": scenario, "sources": sources,
        "cases_sha256": digest(cases), "catalog_sha256": digest(catalog),
        "catalog": catalog, "python": sys.version, "live_calls_enabled": False,
        "paid_call_budget": 0, "main_model_requests": 0,
        "promotion_authorized": False, "prerequisites": PREREQUISITES,
        "isolation": "fresh per-case subprocess/home; Python socket and process guards; not an OS sandbox",
        "worker_sha256": hashlib.sha256((HERE / "worker.py").read_bytes()).hexdigest(),
        "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "comparator_sha256": hashlib.sha256((HERE / "compare.py").read_bytes()).hexdigest(),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    rows_path = output / "observations.jsonl"
    with rows_path.open("x", encoding="utf-8") as stream:
        for case in cases.values():
            for arm in ARMS:
                payload = {"arm": arm, "source_root": str(roots[arm].resolve()),
                           "case": case, "catalog": catalog, "scenario": scenario}
                with tempfile.TemporaryDirectory(prefix=f"{arm}-", dir=output) as temp:
                    home = Path(temp)
                    started = time.perf_counter_ns()
                    try:
                        proc = subprocess.run(
                            [sys.executable, "-I", "-B", str(HERE / "worker.py")],
                            input=json.dumps(payload), text=True, capture_output=True,
                            cwd=home, env=worker_env(home), timeout=20, check=True,
                        )
                        observed = json.loads(proc.stdout)
                    except (subprocess.SubprocessError, ValueError) as exc:
                        observed = {"proposed_model": None, "abstained": True,
                                    "abstention_reason": type(exc).__name__, "implementation_status": "worker_error",
                                    "simulated_requests": 0, "provider_requests": [], "worker_failed": True}
                    elapsed = (time.perf_counter_ns() - started) / 1_000_000
                expected_refs = [f"fixture:{m['model']}" for m in catalog["models"]]
                failed = bool(observed.get("worker_failed"))
                if not failed and (observed.get("task_received") != case["task"] or
                                   observed.get("candidate_ids") != expected_refs):
                    raise ValueError("Worker input parity check failed")
                if not failed and (observed.get("network_attempts") != 0 or observed.get("process_attempts") != 0):
                    raise ValueError("Offline worker attempted network or process escape")
                if not failed and observed.get("loaded_source_root") != str(roots[arm].resolve()):
                    raise ValueError("Loaded source identity differs from pinned checkout")
                if observed.get("provider_requests") != []:
                    raise ValueError("Offline worker reported unexpected provider requests")
                proposed = observed.get("proposed_model")
                abstained = observed.get("abstained")
                if type(abstained) is not bool:
                    raise ValueError("Worker abstention evidence missing")
                decision, reason = safety(case, proposed, catalog, abstained)
                row = {
                    "schema_version": 2, "case_id": case["id"], "arm": arm,
                    "source_sha": sources[arm]["source_sha"], "mode": "shadow", "applied": False,
                    "evidence_kind": "offline_no_response" if scenario == "no-response" else "offline_fixture",
                    "proposed_model": proposed, "would_switch": proposed is not None and proposed != catalog["current"],
                    "current_model": catalog["current"], "approved_candidates": expected_refs,
                    "task_sha256": hashlib.sha256(case["task"].encode()).hexdigest(),
                    "catalog_sha256": digest(catalog), "egress": "none", "external_calls": 0,
                    "provider_requests": [], "simulated_requests": observed.get("simulated_requests", 0),
                    "network_attempts": observed.get("network_attempts"),
                    "process_attempts": observed.get("process_attempts"),
                    "attempt_accounting": "observed_python_guards" if "network_attempts" in observed and "process_attempts" in observed else "unknown_worker_failed",
                    "latency_ms": elapsed, "latency_scope": "subprocess_start_import_and_offline_decision_not_inference",
                    "abstained": abstained, "abstention_reason": observed.get("abstention_reason"),
                    "safety_decision": decision, "safety_reason": reason,
                    "implementation_status": observed.get("implementation_status"), "worker_failed": failed,
                    "raw_decision": observed.get("raw_decision"),
                }
                stream.write(json.dumps(row, sort_keys=True) + "\n")
                stream.flush()
    # Recheck checkout identity after execution, not just before it.
    for arm in ARMS:
        if verify_source(roots[arm], arm) != sources[arm]:
            raise ValueError("Source identity changed during collection")
    # Reparse persisted evidence with the same public comparison gate as the CLI.
    report = compare(cases, read_observations(rows_path, cases))
    (output / "comparison.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--switchyard-source", type=Path, required=True)
    parser.add_argument("--jev-skills-source", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=HERE / "catalog.json")
    parser.add_argument("--offline", choices=("no-response", "synthetic-simple"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    roots = {"switchyard": args.switchyard_source, "jev-skills": args.jev_skills_source}
    try:
        if args.offline:
            if args.output is None:
                parser.error("--offline requires a new --output directory")
            result = collect(roots, args.output, args.offline, args.catalog)
        else:
            result = {"status": "prepared_live_blocked", "sources": {a: verify_source(r, a) for a, r in roots.items()},
                      "catalog_sha256": digest(load_catalog(args.catalog)), "case_count": len(read_cases(HERE / "cases.json")),
                      "paid_call_budget": 0, "live_calls_enabled": False, "prerequisites": PREREQUISITES}
    except (ValueError, OSError, subprocess.SubprocessError, InvalidEvidence) as exc:
        print(f"Collector blocked: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result.get("status") == "incomplete_or_blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
