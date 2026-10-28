"""Read-only, bounded audit of redundant skill_view results after Switchyard preloads.

Evidence comes from retained routing receipts plus Hermes' local state.db. A
timestamp-only join cannot prove a call belonged to the same turn: the report
is observational, never a verified outcome or a billing claim. No transcript
text, skill names, session identifiers, or database paths leave this module.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import stat
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import receipt_history

SCHEMA_VERSION = 1
MAX_WINDOW_SECONDS = 120
MAX_SESSIONS = 128
MAX_ROWS_PER_SESSION = 2000
MAX_RESULT_CHARS = 131072
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _revision(receipt: dict[str, Any]) -> str:
    sha = receipt.get("source_sha")
    return sha if isinstance(sha, str) and _SHA_RE.fullmatch(sha) else "unavailable"


def _event(row: tuple[Any, ...]) -> tuple[float, str | None, str, int]:
    """Classify one skill_view tool result; never return its content."""
    stamp, raw, chars = row
    if not isinstance(raw, str) or type(chars) is not int or chars > MAX_RESULT_CHARS:
        return float(stamp), None, "unknown", 0
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return float(stamp), None, "unknown", 0
    if not isinstance(data, dict):
        return float(stamp), None, "unknown", 0
    name = data.get("name")
    if not isinstance(name, str) or not 0 < len(name) <= 128:
        return float(stamp), None, "unknown", 0
    # Linked references are useful and not redundant copies of SKILL.md.
    if data.get("file") not in (None, "", "SKILL.md"):
        return float(stamp), name, "linked_file", 0
    if (data.get("success") is True and data.get("status") == "unchanged"
            and data.get("dedup") is True and data.get("content_returned") is False):
        return float(stamp), name, "stub", 0
    if data.get("success") is True and isinstance(data.get("content"), str):
        return float(stamp), name, "full", chars
    return float(stamp), name, "unknown", 0


def _safe_db_path(db_path: Path) -> bool:
    try:
        info = db_path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1


def _read_session_events(conn: sqlite3.Connection, session: str, start: float,
                         end: float, active: bool) -> list[tuple[float, str | None, str, int]] | None:
    active_filter = " AND active = 1" if active else ""
    sql = (
        "SELECT timestamp, substr(content, 1, ?), length(content) FROM messages "
        "WHERE session_id = ? AND role = 'tool' AND tool_name = 'skill_view' "
        "AND timestamp >= ? AND timestamp < ?"
        + active_filter + " ORDER BY timestamp, id LIMIT ?"
    )
    rows = conn.execute(sql, (MAX_RESULT_CHARS + 1, session, start, end, MAX_ROWS_PER_SESSION + 1)).fetchall()
    if len(rows) > MAX_ROWS_PER_SESSION:
        return None
    return [_event(row) for row in rows]


def build_report(*, days: int = 7, now: datetime | None = None,
                 db_path: Path | None = None,
                 records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Summarize conservatively matched, completed preload windows.

    Each window starts at its receipt timestamp and stops at the next receipt
    in that session or at 120 seconds, whichever is earlier. The last window
    is pending until the full 120 seconds pass. Exact-name matches only. Results
    of unknown shape disqualify that window rather than becoming a false zero.
    """
    if type(days) is not int or not 1 <= days <= 3650:
        raise ValueError("days must be between 1 and 3650")
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("now must have a timezone")
    moment = moment.astimezone(timezone.utc)
    cutoff = moment - timedelta(days=days)
    rows = records if records is not None else receipt_history.read_history(since=timedelta(days=days), now=moment)
    valid = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        stamp = receipt_history.parse_timestamp(row.get("recorded_at"))
        if stamp is None or not cutoff <= stamp <= moment:
            continue
        sid = receipt_history.sanitize_session_id(row.get("session_id"))
        receipt = row.get("receipt")
        if not isinstance(receipt, dict):
            continue
        valid.append((stamp.timestamp(), sid, receipt))
    valid.sort(key=lambda x: x[0])
    preloads = [r for r in valid if r[2].get("consumer_status") == "loaded"
                and r[2].get("skill_load_verified") is True
                and isinstance(r[2].get("loaded_skill"), str)]
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "unavailable",
        "reason": None,
        "window": {"days": days, "from": receipt_history.format_timestamp(cutoff),
                   "to": receipt_history.format_timestamp(moment)},
        "coverage": {"retained_receipts": len(valid), "preloaded": len(preloads),
                     "eligible": 0, "unmatched_sessions": 0, "ambiguous": 0, "pending": 0},
        "metrics": {"full_refetch_turns": 0, "full_refetch_calls": 0,
                    "unchanged_stub_turns": 0, "different_skill_turns": 0,
                    "full_refetch_rate_pct": None, "redundant_result_chars": 0,
                    "approx_redundant_tokens": 0},
        "by_revision": {},
        "limitations": "Exact-name, same-session, up-to-120s observation; ~4 chars/token heuristic, not billed tokens or proven same-turn causality.",
    }
    if not preloads:
        report["reason"] = "no_retained_preloads"
        return report
    target = db_path if db_path is not None else Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")) / "state.db"
    target = Path(target)
    if not _safe_db_path(target):
        report["reason"] = "state_db_unavailable"
        return report

    groups: dict[str, list[tuple[float, str | None, dict[str, Any]]]] = defaultdict(list)
    for item in valid:
        if item[1] is not None:
            groups[item[1]].append(item)
    selected_sessions = {sid for _, sid, _ in preloads if sid is not None}
    incomplete = False
    if len(selected_sessions) > MAX_SESSIONS:
        report["reason"] = "session_limit_exceeded"
        return report

    summary: dict[str, dict[str, int]] = defaultdict(lambda: {
        "eligible": 0, "full_refetch_turns": 0, "full_refetch_calls": 0,
        "unchanged_stub_turns": 0, "different_skill_turns": 0,
        "redundant_result_chars": 0
    })
    try:
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=0.4)) as conn:
            conn.execute("PRAGMA query_only = ON")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
            if not {"id", "session_id", "role", "content", "tool_name", "timestamp"}.issubset(columns):
                report["reason"] = "state_db_schema_unsupported"
                return report
            has_active = "active" in columns
            for sid in sorted(selected_sessions):
                if not conn.execute("SELECT 1 FROM sessions WHERE id = ? LIMIT 1", (sid,)).fetchone():
                    count = sum(1 for _, s, _ in preloads if s == sid)
                    report["coverage"]["unmatched_sessions"] += count
                    incomplete = True
                    continue
                all_turns = groups[sid]
                start = all_turns[0][0]
                end = min(moment.timestamp() + 0.001, all_turns[-1][0] + MAX_WINDOW_SECONDS + 0.001)
                events = _read_session_events(conn, sid, start, end, has_active)
                if events is None:
                    report["coverage"]["ambiguous"] += sum(
                        1 for _, s, _ in preloads if s == sid
                    )
                    incomplete = True
                    continue
                for i, (stamp, _, receipt) in enumerate(all_turns):
                    if not (receipt.get("consumer_status") == "loaded"
                            and receipt.get("skill_load_verified") is True
                            and isinstance(receipt.get("loaded_skill"), str)):
                        continue
                    next_stamp = all_turns[i + 1][0] if i + 1 < len(all_turns) else None
                    if next_stamp is None and moment.timestamp() - stamp < MAX_WINDOW_SECONDS:
                        report["coverage"]["pending"] += 1
                        incomplete = True
                        continue
                    stop = min(stamp + MAX_WINDOW_SECONDS, next_stamp if next_stamp is not None else float("inf"))
                    if stop <= stamp:
                        report["coverage"]["ambiguous"] += 1
                        incomplete = True
                        continue
                    relevant = [event for event in events if stamp <= event[0] < stop
                                and event[2] != "linked_file"]
                    if any(event[2] == "unknown" for event in relevant):
                        report["coverage"]["ambiguous"] += 1
                        incomplete = True
                        continue
                    selected = receipt["loaded_skill"]
                    matching = [e for e in relevant if e[1] == selected]
                    full = [e for e in matching if e[2] == "full"]
                    stubs = [e for e in matching if e[2] == "stub"]
                    different = any(e[1] != selected for e in relevant)
                    revision = _revision(receipt)
                    bucket = summary[revision]
                    bucket["eligible"] += 1
                    bucket["full_refetch_turns"] += bool(full)
                    bucket["full_refetch_calls"] += len(full)
                    bucket["unchanged_stub_turns"] += bool(stubs)
                    bucket["different_skill_turns"] += different
                    bucket["redundant_result_chars"] += sum(e[3] for e in full)
    except (sqlite3.Error, OSError, ValueError):
        report["reason"] = "state_db_read_failed"
        return report

    report["coverage"]["unmatched_sessions"] += sum(1 for _, sid, _ in preloads if sid is None)
    if report["coverage"]["unmatched_sessions"]:
        incomplete = True
    aggregate = report["metrics"]
    for revision, bucket in sorted(summary.items()):
        report["by_revision"][revision] = dict(bucket)
        for field in ("full_refetch_turns", "full_refetch_calls", "unchanged_stub_turns",
                      "different_skill_turns", "redundant_result_chars"):
            aggregate[field] += bucket[field]
        report["coverage"]["eligible"] += bucket["eligible"]
    if not report["coverage"]["eligible"]:
        report["reason"] = "no_attributable_windows"
        return report
    aggregate["full_refetch_rate_pct"] = round(
        100 * aggregate["full_refetch_turns"] / report["coverage"]["eligible"], 1
    )
    aggregate["approx_redundant_tokens"] = math.ceil(aggregate["redundant_result_chars"] / 4)
    for bucket in report["by_revision"].values():
        bucket["full_refetch_rate_pct"] = round(
            100 * bucket["full_refetch_turns"] / bucket["eligible"], 1
        )
    report["status"] = "partial" if incomplete else "ok"
    return report


def format_text(report: dict[str, Any]) -> str:
    coverage, metrics = report["coverage"], report["metrics"]
    lines = [
        f"Skill duplicate audit: {report['status']} ({report['window']['days']}d, UTC)",
        f"Retained preloads: {coverage['preloaded']} | eligible: {coverage['eligible']} | "
        f"unmatched: {coverage['unmatched_sessions']} | ambiguous: {coverage['ambiguous']} | "
        f"pending: {coverage['pending']}",
    ]
    if report["status"] == "unavailable":
        lines.append(f"Unavailable: {report['reason']}")
    else:
        lines.extend((
            f"Same-skill full rereads: {metrics['full_refetch_turns']}/{coverage['eligible']} "
            f"({metrics['full_refetch_rate_pct']}%), {metrics['full_refetch_calls']} calls",
            f"Same-skill unchanged stubs: {metrics['unchanged_stub_turns']} turns | "
            f"different skill views: {metrics['different_skill_turns']} turns",
            f"Redundant tool-result characters: {metrics['redundant_result_chars']} "
            f"(~{metrics['approx_redundant_tokens']} tokens at 4 chars/token)",
            "By source SHA (compare samples only; no causality claim):",
        ))
        for sha, part in report["by_revision"].items():
            lines.append(f"  {sha[:12]}: {part['full_refetch_turns']}/{part['eligible']} "
                         f"full rereads ({part['full_refetch_rate_pct']}%)")
    lines.append(report["limitations"])
    return "\n".join(lines)
