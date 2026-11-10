"""Isolated, once-only Stage A2 Sol/Luna study collector (stdlib only).

Prepare and inspect are offline. Execute requires an explicit owner-controlled
opt-in, a native Hermes runtime and a separate authentication profile.
No Jev requests, model routing, production config changes or dollar gates.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import random
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# Import the frozen scorer without importing any Hermes modules.
from correctness import APPROVED_MODELS, InvalidEvidence, canonical, digest, load_plan, read_observations, evaluate

HERE = Path(__file__).resolve().parent
PLAN = HERE / "heldout_cases.json"
WORKER = HERE / "stage_a2_native_worker.py"
FROZEN_PLAN_SHA = "6e7ac367e5a3b0fea9dd30648dc640e3169eeb855302e52f190f8d52f65766ef"
SOURCE_FILES = (Path(__file__), WORKER, HERE / "correctness.py", PLAN)
SEED = 20261109
OUTPUT_LIMIT = 128
TIMEOUT_SECONDS = 120
MAX_JOBS = 48


class StudyError(RuntimeError):
    """Unmet study prerequisite. No new requests may be dispatched."""


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_hash(path: Path) -> str:
    return digest(path.read_bytes())


def require_plan() -> tuple[dict, str]:
    tasks, plan_sha = load_plan(PLAN)
    if plan_sha != FROZEN_PLAN_SHA:
        raise StudyError("Frozen Stage A2 plan does not match its approved SHA-256")
    return tasks, plan_sha


def git_identity() -> str:
    repo = HERE.parents[1]
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True, stderr=subprocess.DEVNULL).strip()
        subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "evaluation/jev_skills_comparison", "tests/test_jev_skills_correctness.py"], cwd=repo, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "diff", "--cached", "--quiet", "--", "evaluation/jev_skills_comparison", "tests/test_jev_skills_correctness.py"], cwd=repo, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise StudyError("Evaluation source is not at a clean tracked Git revision") from exc
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        raise StudyError("Invalid Git HEAD identity")
    for p in SOURCE_FILES:
        rel = p.relative_to(repo)
        if subprocess.run(["git", "ls-files", "--error-unmatch", str(rel)], cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            raise StudyError("Evaluation source file is not tracked: " + str(rel))
    return sha


def ordered_pairs(tasks: dict) -> list[tuple[str, str]]:
    ids = sorted(tasks)
    rng = random.Random(SEED)
    rng.shuffle(ids)
    pairs = []
    for case_id in ids:
        models = list(APPROVED_MODELS)
        rng.shuffle(models)
        pairs.extend((case_id, model) for model in models)
    assert len(pairs) == MAX_JOBS and len(set(pairs)) == MAX_JOBS
    return pairs


def metadata() -> dict:
    tasks, plan_sha = require_plan()
    return {
        "schema_version": 1,
        "study": "stage_a2_direct_model_once_only",
        "created_utc": utc(),
        "source_sha": git_identity(),
        "plan_sha256": plan_sha,
        "source_sha256": {p.name: file_hash(p) for p in SOURCE_FILES},
        "provider": "openai-codex",
        "models": list(APPROVED_MODELS),
        "order": [list(p) for p in ordered_pairs(tasks)],
        "max_jobs": MAX_JOBS,
        "output_token_cap": OUTPUT_LIMIT,
        "request_timeout_seconds": TIMEOUT_SECONDS,
        "retry_policy": "no_retry_even_after_unknown_dispatch",
        "monetary_cap": None,
        "gateway_or_production_changes": False,
    }


def atomic_json(path: Path, value: object) -> None:
    tmp = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        dfd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if tmp.exists():
            tmp.unlink()


@contextmanager
def locked(root: Path):
    fd = os.open(root / "run.lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "wb") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StudyError("Another instance owns the study lock") from exc
        yield


def connect(root: Path) -> sqlite3.Connection:
    db = sqlite3.connect(root / "attempts.sqlite3", timeout=10)
    db.execute("PRAGMA journal_mode=DELETE")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("""CREATE TABLE IF NOT EXISTS attempts (
        ordinal INTEGER NOT NULL UNIQUE,
        case_id TEXT NOT NULL,
        model_id TEXT NOT NULL,
        attempt_id TEXT NOT NULL UNIQUE,
        intent_utc TEXT NOT NULL,
        request_sha256 TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('intent','completed','failed','uncertain')),
        observation TEXT,
        UNIQUE(case_id, model_id))""")
    db.commit()
    return db


@contextmanager
def connected(root: Path):
    db = connect(root)
    try:
        with db:
            yield db
    finally:
        db.close()


def load_meta(root: Path) -> dict:
    try:
        recorded = json.loads((root / "manifest.json").read_text())
    except (ValueError, OSError) as exc:
        raise StudyError("Cannot read frozen study metadata") from exc
    current = metadata()
    for key in ("schema_version", "study", "source_sha", "plan_sha256", "source_sha256", "provider", "models", "order", "max_jobs", "output_token_cap", "request_timeout_seconds", "retry_policy"):
        if recorded.get(key) != current.get(key):
            raise StudyError("Study manifest diverged: " + key)
    if recorded.get("monetary_cap") is not None:
        raise StudyError("Unexpected monetary control in this study")
    return recorded


def prepare(root: Path) -> dict:
    meta = metadata()  # validate before touching the requested output path
    if root.resolve().is_relative_to(HERE.parents[1].resolve()):
        raise StudyError("Study output must stay outside the Git repository")
    if root.exists():
        raise StudyError("Study output already exists; refusing to reset any ledger")
    root.mkdir(mode=0o700, parents=False)
    os.chmod(root, 0o700)
    with locked(root):
        atomic_json(root / "manifest.json", meta)
        with connected(root) as db:
            pass
    return meta


def request_body(case: dict, model_id: str) -> dict:
    # Exact public prompt, no tools, history, files, or memory.
    return {"provider": "openai-codex", "model": model_id, "prompt": case["prompt"], "max_output_tokens": OUTPUT_LIMIT,
            "tools": [], "history": [], "fallback": None}


def reserve(db: sqlite3.Connection, meta: dict, ordinal: int, case_id: str, model_id: str, case: dict) -> dict:
    body = request_body(case, model_id)
    intent = {"ordinal": ordinal, "case_id": case_id, "model_id": model_id,
              "attempt_id": "a2-" + uuid.uuid4().hex, "intent_utc": utc(), "body": body,
              "plan_sha256": meta["plan_sha256"], "body_sha256": digest(canonical(body))}
    db.execute("INSERT INTO attempts (ordinal,case_id,model_id,attempt_id,intent_utc,request_sha256,status) VALUES (?,?,?,?,?,?,'intent')",
               (ordinal, case_id, model_id, intent["attempt_id"], intent["intent_utc"], intent["body_sha256"]))
    db.commit()  # durable before worker process creation, never reattempt this pair
    return intent


def eligible_usage(value: object) -> bool:
    return type(value) is int and 0 <= value <= 10_000_000


def normalize_result(result: object, intent: dict, case: dict, elapsed_ms: float) -> tuple[str, dict]:
    """Reject unsupported or contradictory worker claims; retain uncertainty."""
    status = "uncertain"
    answer = None
    input_tokens = output_tokens = None
    receipt = {}
    if isinstance(result, dict):
        effective = result.get("effective_model")
        wanted = intent["model_id"].split(":", 1)[1]
        if (result.get("attempt_id") == intent["attempt_id"]
                and result.get("provider") == "openai-codex"
                and result.get("requested_model") == wanted
                and result.get("wire_model") == wanted
                and result.get("physical_call_count") == 1
                and result.get("fallback_enabled") is False
                and result.get("tools_enabled") is False
                and result.get("completed") is True
                and (effective is None or effective in (wanted, intent["model_id"]))
                and isinstance(result.get("answer"), str)
                and len(result["answer"]) <= 20000
                and eligible_usage(result.get("input_tokens"))
                and eligible_usage(result.get("output_tokens"))):
            status = "completed"
            answer = result["answer"]
            input_tokens = result["input_tokens"]
            output_tokens = result["output_tokens"]
            receipt = {"wire_model": wanted, "effective_model_reported": effective,
                       "runtime_modules_sha256": result.get("runtime_modules_sha256"),
                       "provider_request_id": result.get("provider_request_id") if isinstance(result.get("provider_request_id"), str) else None,
                       "source": "isolated_native_hermes_worker"}
    observation = {"case_id": intent["case_id"], "model_id": intent["model_id"],
                   "plan_sha256": intent["plan_sha256"], "prompt_sha256": digest(case["prompt"].encode("utf-8")),
                   "attempt_id": intent["attempt_id"], "evidence_mode": "observed_direct", "routing_applied": False,
                   "status": status, "answer": answer, "elapsed_ms": round(elapsed_ms, 2),
                   "input_tokens": input_tokens, "output_tokens": output_tokens,
                   "collector_receipt": receipt}
    return status, observation


def worker_command(hermes_command: list[str]) -> list[str]:
    if not isinstance(hermes_command, list) or len(hermes_command) < 3 or not all(isinstance(x, str) for x in hermes_command):
        raise StudyError("Invalid Hermes runtime command")
    entry = "runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)"
    if hermes_command[-1].count(entry) != 1:
        raise StudyError("Hermes runtime invocation does not expose the known native worker seam")
    code = hermes_command[-1].replace(entry, f"sys.argv=[{str(WORKER)!r}]+sys.argv[1:];runpy.run_path({str(WORKER)!r},run_name='__main__')")
    return [*hermes_command[:-1], code]


def discover_runtime() -> list[str]:
    # Read-only CLI query, NOT a gateway launch and NOT a model call.
    try:
        completed = subprocess.run(["hermes", "--print-runtime-command"], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, timeout=20, check=True)
        return worker_command(json.loads(completed.stdout))
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise StudyError("Could not resolve native Hermes runtime command") from exc


def invoke_native(intent: dict, study_root: Path, auth_home: Path, hermes_root: Path, command: list[str]) -> dict:
    # One new worker/home per call, never the shared gateway. Worker home stores no secrets.
    home = study_root / "isolated" / intent["attempt_id"]
    home.mkdir(mode=0o700, parents=False)
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TERM", "HOME") if key in os.environ}
    env.update({"HERMES_HOME": str(auth_home), "HERMES_DISABLE_LAZY_INSTALLS": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    args = [*command, "--home", str(home), "--auth-home", str(auth_home), "--hermes-root", str(hermes_root)]
    job = {"attempt_id": intent["attempt_id"], "model": intent["model_id"], "prompt": intent["body"]["prompt"],
           "output_token_cap": OUTPUT_LIMIT}
    p = subprocess.run(args, input=json.dumps(job) + "\n", text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, env=env, timeout=TIMEOUT_SECONDS)
    if len(p.stdout) > 64_000:
        raise StudyError("Oversized worker receipt; dispatch status unknown")
    if p.returncode != 0:
        raise StudyError("Native Hermes worker failed; dispatch status unknown")
    rows = [line[7:] for line in p.stdout.splitlines() if line.startswith("RESULT ")]
    if len(rows) != 1:
        raise StudyError("Missing or multiple worker receipts; dispatch status unknown")
    return json.loads(rows[0])


def record(db: sqlite3.Connection, intent: dict, status: str, observation: dict) -> None:
    changed = db.execute("UPDATE attempts SET status=?, observation=? WHERE attempt_id=? AND status='intent'",
               (status, canonical(observation).decode("utf-8"), intent["attempt_id"]))
    if changed.rowcount != 1:
        raise StudyError("Could not finalize immutable attempt")
    db.commit()


def export(root: Path, db: sqlite3.Connection, meta: dict, tasks: dict) -> dict:
    """Read authoritative DB rows; an unfinalized intent remains uncertain, never replayed."""
    records = db.execute("SELECT ordinal,case_id,model_id,attempt_id,status,observation FROM attempts ORDER BY ordinal").fetchall()
    rows = []
    for ordinal, case_id, model_id, attempt_id, status, raw in records:
        if raw is not None:
            row = json.loads(raw)
        else:
            row = {"case_id": case_id, "model_id": model_id, "plan_sha256": meta["plan_sha256"],
                   "prompt_sha256": digest(tasks[case_id]["prompt"].encode("utf-8")),
                   "attempt_id": attempt_id, "evidence_mode": "observed_direct", "routing_applied": False,
                   "status": "uncertain", "answer": None, "elapsed_ms": None,
                   "input_tokens": None, "output_tokens": None,
                   "collector_receipt": {"reason": "durable_intent_unresolved"}}
        rows.append(row)
    payload = b"".join(canonical(r) + b"\n" for r in rows)
    out = root / "observations.jsonl"
    tmp = out.with_name("." + out.name + "." + uuid.uuid4().hex)
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, out)
        dirfd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        if tmp.exists():
            tmp.unlink()
    parsed = read_observations(out, tasks, meta["plan_sha256"])
    report = evaluate(tasks, parsed, meta["plan_sha256"])
    report["collector_intents_persisted"] = len(records)
    report["collector_unresolved_intents"] = sum(r[4] == "intent" for r in records)
    report["provider_calls_independently_verified"] = False
    report["promotion_authorized"] = False
    atomic_json(root / "comparison.json", report)
    return report


def execute(root: Path, auth_home: Path, hermes_root: Path, *, max_new: int, runner=None) -> dict:
    tasks, _ = require_plan()
    if not auth_home.is_dir() or not hermes_root.is_dir():
        raise StudyError("Explicit Hermes authentication home and runtime source directory required")
    if (root.resolve().is_relative_to(auth_home.resolve()) or
            auth_home.resolve().is_relative_to(root.resolve())):
        raise StudyError("Study output and credential home must be separate")
    if max_new not in range(1, MAX_JOBS + 1):
        raise StudyError("New-call count must be between 1 and 48")
    with locked(root):
        meta = load_meta(root)
        with connected(root) as db:
            if db.execute("SELECT count(*) FROM attempts WHERE status IN ('intent','uncertain','failed')").fetchone()[0]:
                # Never continue after an uncertain attempt; evaluate and seek owner review.
                export(root, db, meta, tasks)
                raise StudyError("Unresolved prior intent: stop, preserve evidence and do not replay")
            remaining = [(i, cid, mod) for i, (cid, mod) in enumerate(meta["order"]) if
                         not db.execute("SELECT 1 FROM attempts WHERE case_id=? AND model_id=?", (cid, mod)).fetchone()]
            if not remaining:
                return export(root, db, meta, tasks)
            command = None if runner is not None else discover_runtime()
            (root / "isolated").mkdir(mode=0o700, exist_ok=True)
            new_count = 0
            for ordinal, case_id, model_id in remaining[:max_new]:
                # Re-validate code/plan and provider account prerequisites before each intent.
                load_meta(root)
                intent = reserve(db, meta, ordinal, case_id, model_id, tasks[case_id])
                start = time.perf_counter()
                try:
                    result = (runner(intent) if runner is not None else
                              invoke_native(intent, root, auth_home, hermes_root, command))
                    elapsed = (time.perf_counter() - start) * 1000
                    status, obs = normalize_result(result, intent, tasks[case_id], elapsed)
                except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                    elapsed = (time.perf_counter() - start) * 1000
                    status, obs = normalize_result(None, intent, tasks[case_id], elapsed)
                record(db, intent, status, obs)
                new_count += 1
                if status != "completed":
                    break  # no retry; stop on all unknown or incomplete outcomes
            report = export(root, db, meta, tasks)
            report["new_intents_this_run"] = new_count
            return report


def inspect(root: Path) -> dict:
    tasks, _ = require_plan()
    with locked(root):
        meta = load_meta(root)
        with connected(root) as db:
            return export(root, db, meta, tasks)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--inspect", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--auth-home", type=Path)
    parser.add_argument("--hermes-root", type=Path)
    parser.add_argument("--max-new-requests", type=int, default=1)
    parser.add_argument("--authorize-direct-48", action="store_true", help="Confirm owner authorizes this finite 48-request direct-model study")
    args = parser.parse_args(argv)
    try:
        if args.prepare:
            result = prepare(args.output)
        elif args.inspect:
            result = inspect(args.output)
        else:
            if not args.authorize_direct_48 or args.auth_home is None or args.hermes_root is None:
                raise StudyError("Execution requires --authorize-direct-48, --auth-home and --hermes-root")
            result = execute(args.output, args.auth_home.resolve(), args.hermes_root.resolve(), max_new=args.max_new_requests)
    except (StudyError, InvalidEvidence, OSError, sqlite3.Error) as exc:
        print("Stage A2 blocked: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.execute and result["status"] == "incomplete_or_unresolved" and result.get("new_intents_this_run", 0) == 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
