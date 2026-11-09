"""Read-only source and running-gateway evidence without an IPC attestation.

A checkout SHA is not evidence about the loaded process. This command reports
source provenance, host service timing (when available), and new gateway-like
receipts separately; it never claims to inspect Python modules in another PID.
"""
from __future__ import annotations

import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from . import receipt_history

_SHA = re.compile(r"^[0-9a-f]{40}$")
_STARTED = re.compile(r"\b(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC\b")


def _sha(value: Any) -> str:
    return value if isinstance(value, str) and _SHA.fullmatch(value) else "unavailable"


def _gateway_service() -> dict[str, Any]:
    """Use bounded, non-shell systemd inspection; unavailable elsewhere."""
    result: dict[str, Any] = {"status": "unavailable", "pid": None, "started_at": None}
    try:
        process = subprocess.run(
            ["systemctl", "--user", "show", "hermes-gateway.service",
             "-p", "MainPID", "-p", "ActiveEnterTimestamp", "--no-pager"],
            capture_output=True, text=True, timeout=2.0, check=False,
        )
        if process.returncode != 0 or len(process.stdout) > 2048:
            return result
        values = {}
        for line in process.stdout.splitlines():
            key, sep, value = line.partition("=")
            if sep and key in {"MainPID", "ActiveEnterTimestamp"}:
                values[key] = value.strip()
        pid_raw = values.get("MainPID", "")
        match = _STARTED.search(values.get("ActiveEnterTimestamp", ""))
        if not pid_raw.isdecimal() or int(pid_raw) <= 0 or not match:
            return result
        started = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        result.update({
            "status": "running", "pid": int(pid_raw),
            "started_at": receipt_history.format_timestamp(started),
        })
        return result
    except (OSError, subprocess.SubprocessError, ValueError, OverflowError):
        return result


def report(*, days: int = 7, now: datetime | None = None,
           records: list[dict[str, Any]] | None = None,
           service: dict[str, Any] | None = None) -> dict[str, Any]:
    if type(days) is not int or not 1 <= days <= 3650:
        raise ValueError("days must be between 1 and 3650")
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    local_git = _sha(receipt_history.resolve_git_source_sha())
    local_receipt = _sha(receipt_history.resolve_receipt_source_sha())
    svc = service if service is not None else _gateway_service()
    started = receipt_history.parse_timestamp(svc.get("started_at")) if isinstance(svc, dict) else None
    if started is not None and started > moment:
        started = None
    history = records if records is not None else receipt_history.read_history(
        since=timedelta(days=days), now=moment
    )
    by_revision: Counter[str] = Counter()
    gateway_after_start: Counter[str] = Counter()
    latest: tuple[datetime, str] | None = None
    retained = 0
    for record in history:
        if not isinstance(record, dict):
            continue
        stamp = receipt_history.parse_timestamp(record.get("recorded_at"))
        receipt = record.get("receipt")
        if stamp is None or not isinstance(receipt, dict) or not moment - timedelta(days=days) <= stamp <= moment:
            continue
        revision = _sha(receipt.get("source_sha"))
        by_revision[revision] += 1
        retained += 1
        if latest is None or latest[0] < stamp:
            latest = (stamp, revision)
        # CLI receipts do not demonstrate a gateway process ran anything.
        platform = receipt_history.sanitize_platform(record.get("platform"))
        if started is not None and started <= stamp and platform not in (None, "cli"):
            gateway_after_start[revision] += 1
    matching = gateway_after_start.get(local_git, 0) if local_git != "unavailable" else 0
    observed = bool(
        matching and local_git == local_receipt
        and isinstance(svc, dict) and svc.get("status") == "running"
    )
    return {
        "schema_version": 1,
        "status": "runtime_receipt_observed" if observed else "unverified",
        "installed_git_sha": local_git,
        "cli_receipt_source_sha": local_receipt,
        "installed_sources_consistent": (
            local_git == local_receipt
            if local_git != "unavailable" and local_receipt != "unavailable" else None
        ),
        "gateway_service": {
            "status": svc.get("status", "unavailable") if isinstance(svc, dict) else "unavailable",
            "pid": svc.get("pid") if isinstance(svc, dict) and type(svc.get("pid")) is int else None,
            "started_at": receipt_history.format_timestamp(started) if started else None,
        },
        "days": days,
        "retained_receipts": retained,
        "receipts_by_revision": dict(sorted(by_revision.items())),
        "gateway_like_receipts_since_service_start_by_revision": dict(sorted(gateway_after_start.items())),
        "matching_post_start_receipts": matching,
        "latest_receipt_source_sha": latest[1] if latest else None,
        "latest_receipt_at": receipt_history.format_timestamp(latest[0]) if latest else None,
        "gateway_runtime_verified": False,
        "limitations": (
            "Post-service-start non-CLI receipts are evidence consistent with a "
            "gateway runtime, not an attestation of code loaded in the PID. "
            "Profile-specific history may be incomplete; other profiles may share "
            "the same gateway. Revisions do not prove a feature was exercised."
        ),
    }


def format_text(report: dict[str, Any]) -> str:
    service = report["gateway_service"]
    return "\n".join([
        "Switchyard runtime provenance: " + report["status"],
        "Checkout Git: " + report["installed_git_sha"],
        "CLI receipt source: " + report["cli_receipt_source_sha"],
        f"Gateway service: {service['status']} (PID {service['pid']}, started {service['started_at']})",
        f"Matching post-start gateway-like receipts: {report['matching_post_start_receipts']}",
        report["limitations"],
    ])
