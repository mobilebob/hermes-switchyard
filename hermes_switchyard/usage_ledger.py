"""Optional hash-free, payload-free Jev request ledger.

Records a logical request intent before the real provider exchange and a
terminal observation afterward. A missing completion or a failure is UNKNOWN
spend, not a free request. No state, answers, question text, keys, paths,
provider request IDs, or session identifiers are stored.

The live hook is disabled by default; enable explicitly through the gateway's
SWITCHYARD_JEV_USAGE_LEDGER=on environment. Synthetic transports are excluded.
"""
from __future__ import annotations

import json
import math
import os
import re
import stat
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import receipt_state

FILENAME = "jev-usage-ledger.jsonl"
MAX_BYTES = 8 * 1024 * 1024
MAX_LINE = 768
_SCHEMA = 1
_ID = re.compile(r"^[0-9a-f]{32}$")
_STAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
_PROVIDERS = {"typesafe", "openrouter", "unknown"}
_PHASES = {"begin", "finish"}
_OUTCOMES = {"validated", "unresolved"}
_ENV = "SWITCHYARD_JEV_USAGE_LEDGER"


def enabled() -> bool:
    return os.environ.get(_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _path(data_dir: Path | str | None = None) -> Path | None:
    root = Path(data_dir) if data_dir is not None else receipt_state._plugin_data_dir()
    return root / FILENAME if root is not None else None


def _timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_stamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not _STAMP.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _finite_cost(value: Any) -> bool:
    if type(value) not in (int, float) or value < 0:
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _row_valid(row: Any) -> bool:
    if not isinstance(row, dict) or type(row.get("schema")) is not int or row["schema"] != _SCHEMA:
        return False
    if row.get("phase") not in _PHASES or row.get("provider") not in _PROVIDERS:
        return False
    if not isinstance(row.get("id"), str) or not _ID.fullmatch(row["id"]):
        return False
    if _parse_stamp(row.get("at")) is None:
        return False
    if row["phase"] == "begin":
        return set(row) == {"schema", "phase", "id", "at", "provider"}
    if set(row) != {"schema", "phase", "id", "at", "provider", "outcome", "cost_usd", "total_tokens", "transport_retries"}:
        return False
    if row["outcome"] not in _OUTCOMES:
        return False
    cost = row["cost_usd"]
    tokens = row["total_tokens"]
    retries = row["transport_retries"]
    return (
        (cost is None or _finite_cost(cost))
        and (tokens is None or (type(tokens) is int and 0 <= tokens <= 10**12))
        and type(retries) is int and 0 <= retries <= 100
        and (row["outcome"] != "unresolved" or (cost is None and tokens is None))
    )


def _append(row: dict[str, Any], data_dir: Path | str | None = None) -> bool:
    """Append a bounded record without following symlinks or breaking a turn."""
    if not _row_valid(row):
        return False
    path = _path(data_dir)
    if path is None:
        return False
    encoded = (json.dumps(row, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n").encode("ascii")
    if len(encoded) > MAX_LINE:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Explicitly reject existing symlinks on platforms without O_NOFOLLOW.
        try:
            previous = path.lstat()
        except FileNotFoundError:
            previous = None
        if previous is not None and not stat.S_ISREG(previous.st_mode):
            return False
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or (os.name != "nt" and info.st_mode & 0o077)
                    or info.st_size + len(encoded) > MAX_BYTES):
                return False
            # One short append record per file write; flush before the caller
            # attempts the provider request. This has latency cost when opt-in.
            if os.write(fd, encoded) != len(encoded):
                return False
            os.fsync(fd)
            return True
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return False


def begin(provider: str, *, data_dir: Path | str | None = None,
          now: datetime | None = None, force: bool = False) -> str | None:
    """Return an opaque attempt ID only when its durable begin was recorded."""
    if not force and not enabled():
        return None
    if provider not in _PROVIDERS:
        provider = "unknown"
    attempt = uuid.uuid4().hex
    row = {"schema": _SCHEMA, "phase": "begin", "id": attempt,
           "at": _timestamp(now), "provider": provider}
    return attempt if _append(row, data_dir) else None


def finish(attempt: str | None, provider: str, *, outcome: str,
           usage: Any = None, retries: Any = None,
           data_dir: Path | str | None = None, now: datetime | None = None) -> bool:
    """Never infer a zero cost from an absent cost field."""
    if not isinstance(attempt, str) or not _ID.fullmatch(attempt):
        return False
    if provider not in _PROVIDERS:
        provider = "unknown"
    if outcome not in _OUTCOMES:
        return False
    cost = None
    tokens = None
    if outcome == "validated" and isinstance(usage, dict):
        raw_cost = usage.get("cost")
        if _finite_cost(raw_cost):
            cost = float(raw_cost)
        raw_tokens = usage.get("total_tokens")
        if (type(raw_tokens) in (int, float) and 0 <= raw_tokens <= 10**12
                and float(raw_tokens).is_integer()):
            tokens = int(raw_tokens)
    retry_count = 0
    if isinstance(retries, dict):
        for value in retries.values():
            if type(value) is int and 0 <= value <= 100:
                retry_count = min(100, retry_count + value)
    row = {"schema": _SCHEMA, "phase": "finish", "id": attempt,
           "at": _timestamp(now), "provider": provider, "outcome": outcome,
           "cost_usd": cost, "total_tokens": tokens,
           "transport_retries": retry_count}
    return _append(row, data_dir)


def report(*, days: int = 7, data_dir: Path | str | None = None,
           now: datetime | None = None) -> dict[str, Any]:
    if type(days) is not int or not 1 <= days <= 3650:
        raise ValueError("days must be in 1..3650")
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result: dict[str, Any] = {
        "schema_version": _SCHEMA, "status": "unavailable",
        "reason": None, "days": days, "capture_enabled_in_this_cli": enabled(),
        "begin_count": 0, "validated": 0, "unresolved": 0,
        "pending": 0, "orphan_finishes": 0, "invalid_lines": 0,
        "known_cost_requests": 0, "unknown_cost_requests": 0,
        "known_cost_usd": None, "observed_total_tokens": 0,
        "known_token_requests": 0, "transport_retries": 0,
        "by_provider": {}, "billing_verified": False,
        "limitations": ("Logical request attempts, not HTTP sends or billed invoices; "
                        "stale retries and rate-limit retries can resend. "
                        "Pending/error/unknown cost is never treated as zero. "
                        "Capture is opt-in in the gateway process; this CLI flag "
                        "does not establish the gateway environment.")
    }
    path = _path(data_dir)
    if path is None:
        result["reason"] = "ledger_path_unavailable"
        return result
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
            result["reason"] = "ledger_unreadable"
            return result
        with path.open("rb") as handle:
            lines = handle.readlines()
    except OSError:
        result["reason"] = "no_ledger"
        return result
    if not lines:
        result["reason"] = "empty_ledger"
        return result
    invalid = 0
    began: dict[str, dict[str, Any]] = {}
    finished: dict[str, dict[str, Any]] = {}
    for raw in lines:
        if len(raw) > MAX_LINE or not raw.endswith(b"\n"):
            invalid += 1
            continue
        try:
            row = json.loads(raw)
        except (UnicodeError, ValueError, TypeError):
            invalid += 1
            continue
        if not _row_valid(row):
            invalid += 1
            continue
        target = began if row["phase"] == "begin" else finished
        if row["id"] in target:
            invalid += 1
            continue
        target[row["id"]] = row
    result["invalid_lines"] = invalid
    result["storage_near_capacity"] = info.st_size >= MAX_BYTES - MAX_LINE
    cutoff = moment - timedelta(days=days)
    by_provider: dict[str, Counter] = {}
    total = 0.0
    for attempt, start in began.items():
        started = _parse_stamp(start["at"])
        if started is None or not cutoff <= started <= moment:
            continue
        result["begin_count"] += 1
        provider = start["provider"]
        bucket = by_provider.setdefault(provider, Counter())
        bucket["attempts"] += 1
        end = finished.get(attempt)
        if end is None:
            result["pending"] += 1
            bucket["pending"] += 1
            continue
        if end["provider"] != provider or _parse_stamp(end["at"]) < started:
            result["invalid_lines"] += 1
            result["unresolved"] += 1
            bucket["unresolved"] += 1
            continue
        if end["outcome"] == "unresolved":
            result["unresolved"] += 1
            bucket["unresolved"] += 1
            continue
        result["validated"] += 1
        bucket["validated"] += 1
        result["transport_retries"] += end["transport_retries"]
        if end["cost_usd"] is not None:
            result["known_cost_requests"] += 1
            total += end["cost_usd"]
        if end["total_tokens"] is not None:
            result["known_token_requests"] += 1
            result["observed_total_tokens"] += end["total_tokens"]
    result["orphan_finishes"] = sum(1 for attempt in finished if attempt not in began)
    result["unknown_cost_requests"] = result["begin_count"] - result["known_cost_requests"]
    result["known_cost_usd"] = round(total, 8) if result["known_cost_requests"] else None
    result["by_provider"] = {name: dict(count) for name, count in sorted(by_provider.items())}
    if result["begin_count"] == 0:
        result["reason"] = "no_attempts_in_window"
    else:
        result["status"] = (
            "partial" if result["pending"] or result["unresolved"]
            or result["invalid_lines"] or result["orphan_finishes"]
            or result["unknown_cost_requests"] or result["storage_near_capacity"] else "ok"
        )
    return result
