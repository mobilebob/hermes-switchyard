"""Credential-free native decision worker; all external requests use parent IPC."""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import importlib
import io
import json
from pathlib import Path
import sys

import worker as isolated
from live_transport import MODEL, canonical, strict_loads, Uncertain
from collect import verify_source

CATALOG = {
    "provider": "openai-codex", "current": "openai-codex:gpt-6.1-sol",
    "metadata_note": "Conservative 4096-token evaluation context floor; reference input-price ordering only, not Codex billing or measured quality.",
    "models": [
        {"id": "gpt-6-luna", "model": "gpt-6-luna", "cost": 0.1, "context_limit": 4096,
         "description": "OpenAI efficient general text model for focused tasks"},
        {"id": "gpt-6.1-sol", "model": "gpt-6.1-sol", "cost": 2.0, "context_limit": 4096,
         "description": "OpenAI general text model for complex coding and professional work"},
    ],
}


def validate_input(payload):
    if not isinstance(payload, dict) or set(payload) != {"arm", "source_root", "case", "catalog"}:
        raise ValueError("invalid_worker_input")
    if payload["arm"] not in ("switchyard", "jev-skills") or payload["catalog"] != CATALOG:
        raise ValueError("unapproved_arm_or_catalog")
    frozen = strict_loads(Path(__file__).with_name("cases.json").read_bytes())
    cases = {case["id"]: case for case in frozen["cases"]}
    supplied = payload["case"]
    if not isinstance(supplied, dict) or set(supplied) != {"id", "task"}:
        raise ValueError("invalid_case_input")
    case = cases.get(supplied["id"])
    if case is None or supplied["task"] != case["task"] or case["egress_limit"] != "redacted_text":
        raise ValueError("private_or_noncanonical_case")
    verify_source(Path(payload["source_root"]), payload["arm"])


def decide(payload, perform):
    root = Path(payload["source_root"]).resolve()
    task = payload["case"]["task"]
    catalog = payload["catalog"]
    models = catalog["models"]
    provider = catalog["provider"]
    refs = [provider + ":" + row["model"] for row in models]
    if payload["arm"] == "switchyard":
        policy = isolated._load_module(root, "hermes_switchyard", "model_policy")
        client_module = importlib.import_module("hermes_switchyard.client")
        client = client_module.DecisionClient(api_key="parent-transport-owned", model=MODEL,
            endpoint="https://api.typesafe.ai/v1/systemone", transport=perform)
        registry = [{"id": row["id"], "provider": provider, "model": row["model"],
            "account": "owner-approved-existing-codex-account", "approved": True,
            "data_classes_allowed": ["public"], "tool_capabilities": [],
            "context_limit": row["context_limit"], "cost": row["cost"], "description": row["description"]}
            for row in models]
        result = policy.recommend_approved_model(task=task, requirements={"data_classes": ["public"]},
            registry=registry, registry_version="stage-a-owner-approved-real-pair-v1",
            valid_until="2030-01-01T00:00:00Z", now=datetime(2026, 1, 1, tzinfo=timezone.utc),
            client=client, public_or_sanitized_data_ack=True)
        proposed = next((provider + ":" + m["model"] for m in models if m["id"] == result.get("selected")), None)
        reason = result.get("abstention_reason")
        package = "hermes_switchyard"
    else:
        route = isolated._load_module(root, "jevkit", "route")
        native_ask = route.client.ask
        def transport(body, _headers, _timeout):
            try:
                return canonical(perform(strict_loads(body)))
            except Exception:
                raise route.client.JevError("shadow_no_retry", "parent broker blocked or uncertain") from None
        def ask(state, questions, **_kwargs):
            return native_ask(state, questions, provider="typesafe", model=MODEL,
                api_key="parent-transport-owned", timeout=15, retries=0, transport=transport)
        route.client.ask = ask
        ordered = sorted(models, key=lambda row: row["cost"])
        tiers = {}
        for index, tier in enumerate(route.TIERS):
            pool = [provider + ":" + m["model"] for rank, m in enumerate(ordered)
                    if min(2, rank * 3 // len(ordered)) == index]
            if not pool:
                pool = [provider + ":" + ordered[min(len(ordered)-1, index * len(ordered)//3)]["model"]]
            tiers[tier] = {specialty: list(pool) for specialty in route.SPECIALTIES}
        config = json.loads(json.dumps(route.DEFAULT_CONFIG))
        config.update({"cache_repeat_asks": False, "cache_size": 0, "exclude": [], "private_profiles": [],
            "tiers": tiers, "escalation": {"enabled": False, "rungs": []}, "ask_sections": [],
            "drop_sections": [], "ask_chars": max(2500, len(task))})
        rows = [{"provider": provider, "model": m["model"], "price": m["cost"],
                 "context": m["context_limit"], "vision": False, "released": ""} for m in models]
        result = route.decide(task, current=catalog["current"], only_provider=provider,
            pinned=payload["case"]["id"] == "manual-model-pin", context_tokens=0, has_images=False,
            config=config, rows=rows, timeout=15)
        proposed = result.get("model") if result.get("tier") else None
        reason = result.get("reason") if proposed is None else None
        package = "jevkit"
    isolated._assert_not_applied(result)
    if proposed is not None and proposed not in refs:
        raise ValueError("outside_approved_models")
    return {"proposed_model": proposed, "abstained": proposed is None, "abstention_reason": reason,
            "raw_decision": {k: v for k, v in result.items() if k in isolated._DECISION_FIELDS},
            "task_received": task, "candidate_ids": refs,
            "loaded_source_root": isolated._verified_source_root(root, package), "worker_failed": False}


def main():
    wire = sys.stdout
    attempts = {"network": 0, "process": 0}
    count = 0
    def perform(body):
        nonlocal count
        count += 1
        if count != 1:
            raise Uncertain("one_native_request_per_case_only")
        wire.write(json.dumps({"kind": "provider_request", "payload": body}) + "\n")
        wire.flush()
        response = strict_loads(sys.stdin.readline())
        if "response" not in response:
            raise Uncertain("parent_unavailable_no_retry")
        return response["response"]
    try:
        raw = sys.stdin.buffer.readline(isolated.MAX_INPUT_BYTES + 1)
        if len(raw) > isolated.MAX_INPUT_BYTES:
            raise ValueError("oversized_input")
        payload = strict_loads(raw)
        validate_input(payload)
        isolated._deny_external_operations(attempts)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = decide(payload, perform)
    except Exception as exc:
        result = {"proposed_model": None, "abstained": True, "abstention_reason": "worker_error",
                  "worker_failed": True, "raw_decision": {"error_type": type(exc).__name__}}
    result.update({"kind": "decision", "request_intents": count,
                   "network_attempts": attempts["network"], "process_attempts": attempts["process"]})
    wire.write(json.dumps(result) + "\n")
    wire.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
