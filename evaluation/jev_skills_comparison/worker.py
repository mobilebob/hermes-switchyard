"""One-process, stdin/stdout adapter for pinned, offline routing functions.

The parent verifies source identity and supplies a secret-free environment. This
worker loads only routing modules (not plugin entrypoints), denies network and
process launches, and never applies a route. ``synthetic-simple`` is a fixed
fixture, not inference; ``no-response`` raises an explicit stub failure. Request
counts count attempted stub calls, including failures, not provider exchanges.

Switchyard's pinned protocol is ``client.decide``, not ``client.ask``. Jev's
pinned client returns answer dictionaries, not an Answer class. The adapters
below follow those actual APIs. Jev pools use cost-ranked thirds of the supplied
catalog, shared across specialties; empty tiers repeat the nearest ranked model.
No vision capability is asserted by this input schema. Neither arm receives the
case's may_switch, risk, or egress_limit evaluation labels.
"""
from __future__ import annotations

import contextlib
import importlib
import importlib.machinery
import io
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import types
from datetime import datetime, timezone
from typing import Any

MAX_INPUT_BYTES = 1_000_000
MAX_STUB_CALLS = 64
MAX_STUB_BYTES = 96_000
MAX_STUB_QUESTIONS = 255


class OfflineDenied(RuntimeError):
    """A prohibited network or process operation was attempted."""


class FixtureNoResponse(RuntimeError):
    """The no-response fixture deliberately supplied no answer."""


class RouteApplicationDenied(RuntimeError):
    """The advisory API unexpectedly reported a route application."""


def _deny_external_operations(attempts: dict[str, int]) -> None:
    def network_denied(*_args: Any, **_kwargs: Any) -> Any:
        attempts["network"] += 1
        raise OfflineDenied()

    def process_denied(*_args: Any, **_kwargs: Any) -> Any:
        attempts["process"] += 1
        raise OfflineDenied()

    for name in ("connect", "connect_ex", "send", "sendall", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            setattr(socket.socket, name, network_denied)
    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex",
                 "gethostbyaddr"):
        setattr(socket, name, network_denied)
    subprocess.Popen = process_denied
    for name in ("system", "fork", "forkpty", "posix_spawn", "posix_spawnp", "execv",
                 "execve", "execvp", "execvpe", "execl", "execle", "execlp", "execlpe",
                 "spawnv", "spawnve", "spawnvp", "spawnvpe", "spawnl", "spawnle",
                 "spawnlp", "spawnlpe"):
        if hasattr(os, name):
            setattr(os, name, process_denied)


def _load_module(root: Path, package: str, module: str) -> Any:
    """Use the exact source tree without executing its plugin __init__.py."""
    directory = root / package
    if not directory.is_dir() or not (directory / (module + ".py")).is_file():
        raise FileNotFoundError()
    if any(name == package or name.startswith(package + ".") for name in sys.modules):
        raise RuntimeError()  # One arm per fresh interpreter; never reuse imports.
    namespace = types.ModuleType(package)
    namespace.__path__ = [str(directory)]
    namespace.__package__ = package
    namespace.__spec__ = importlib.machinery.ModuleSpec(package, loader=None, is_package=True)
    namespace.__spec__.submodule_search_locations = namespace.__path__
    sys.modules[package] = namespace
    loaded = importlib.import_module(package + "." + module)
    loaded_file = getattr(loaded, "__file__", None)
    if not isinstance(loaded_file, str) or Path(loaded_file).resolve() != (directory / (module + ".py")).resolve():
        raise RuntimeError()
    return loaded


def _text(value: Any) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError()
    return value


def _verified_source_root(root: Path, package: str) -> str:
    """Every loaded source module must belong to the supplied source tree."""
    found = False
    for name, module in tuple(sys.modules.items()):
        if not name.startswith(package + "."):
            continue
        filename = getattr(module, "__file__", None)
        if not isinstance(filename, str):
            raise RuntimeError()
        if not Path(filename).resolve().is_relative_to(root / package):
            raise RuntimeError()
        found = True
    if not found:
        raise RuntimeError()
    return str(root)


def _catalog(value: Any) -> tuple[str, str | None, list[dict[str, Any]], list[str]]:
    if not isinstance(value, dict):
        raise ValueError()
    if value.get("scope") != "synthetic-offline-only":
        raise ValueError()
    provider = _text(value.get("provider"))
    if provider != "fixture":
        raise ValueError()
    current = value.get("current")
    if current is not None:
        current = _text(current)
    models = value.get("models")
    if not isinstance(models, list) or not 1 <= len(models) <= 1000:
        raise ValueError()
    clean, refs, identifiers = [], [], set()
    for row in models:
        if not isinstance(row, dict):
            raise ValueError()
        if row.get("approved") is not True:
            raise ValueError()
        identifier, model = _text(row.get("id")), _text(row.get("model"))
        if ":" in identifier or ":" in model:
            raise ValueError()
        cost, context = row.get("cost"), row.get("context_limit")
        description = row.get("description")
        if not isinstance(cost, (int, float)) or isinstance(cost, bool):
            raise ValueError()
        if (identifier in identifiers or not math.isfinite(cost) or cost < 0 or type(context) is not int
                or context <= 0 or type(description) is not str):
            raise ValueError()
        ref = provider + ":" + model
        if ref in refs:
            raise ValueError()
        identifiers.add(identifier)
        refs.append(ref)
        clean.append({"id": identifier, "model": model, "cost": cost,
                      "context_limit": context, "description": description})
    if current not in refs:
        raise ValueError()
    return provider, current, clean, refs


class FixtureClient:
    """Bounded stub shared by both arm adapters; no real client is constructed."""

    def __init__(self, scenario: str, calls: list[dict[str, Any]]) -> None:
        self.scenario = scenario
        self.calls = calls

    def record(self, method: str, state: Any, questions: Any) -> None:
        # Record before validation/failure so attempts cannot disappear.
        self.calls.append({"method": method, "question_ids": list(questions) if isinstance(questions, dict) else [],
                           "outcome": "attempted"})
        if (len(self.calls) > MAX_STUB_CALLS or not isinstance(questions, dict)
                or not 1 <= len(questions) <= MAX_STUB_QUESTIONS
                or len(json.dumps({"state": state, "questions": questions},
                                  allow_nan=False).encode("utf-8")) > MAX_STUB_BYTES):
            raise ValueError()
        if self.scenario == "no-response":
            self.calls[-1]["outcome"] = "fixture_no_response"
            raise FixtureNoResponse()
        self.calls[-1]["outcome"] = "synthetic_fixture"

    def decide(self, state: Any, questions: Any, *, public_or_sanitized_data_ack: bool = False) -> dict:
        self.record("decide", state, questions)
        if public_or_sanitized_data_ack is not True:
            raise ValueError()
        if any(q.get("type") != "noul" for q in questions.values()):
            raise ValueError()
        return {"answers": {name: {"noul": 0.99} for name in questions},
                "model": "offline-synthetic-fixture", "latency_ms": 0,
                "usage": {}, "request_count": 1}


def _switchyard(root: Path, task: str, provider: str, models: list[dict],
                context_tokens: int, stub: FixtureClient) -> tuple[dict, str | None, str | None]:
    policy = _load_module(root, "hermes_switchyard", "model_policy")
    registry = [{"id": row["id"], "provider": provider, "model": row["model"],
                 "account": "offline-fixture", "approved": True,
                 "data_classes_allowed": ["public"], "tool_capabilities": [],
                 "cost": row["cost"], "context_limit": row["context_limit"],
                 "description": row["description"]} for row in models]
    requirements: dict[str, Any] = {"data_classes": ["public"]}
    if context_tokens:
        requirements["context_limit"] = context_tokens
    decision = policy.recommend_approved_model(
        task=task, requirements=requirements, registry=registry,
        registry_version="stage-a-offline-fixture-v1", valid_until="2030-01-01T00:00:00Z",
        now=datetime(2026, 1, 1, tzinfo=timezone.utc), client=stub,
        public_or_sanitized_data_ack=True,
    )
    by_id = {row["id"]: provider + ":" + row["model"] for row in models}
    selected = decision.get("selected")
    proposed = by_id.get(selected) if selected is not None else None
    if selected is not None and proposed is None:
        raise ValueError()
    return decision, proposed, decision.get("abstention_reason")


def _jev(root: Path, task: str, provider: str, current: str | None, models: list[dict],
         context_tokens: int, has_images: bool, pinned: bool,
         stub: FixtureClient) -> tuple[dict, str | None, str | None]:
    route = _load_module(root, "jevkit", "route")

    def ask(state: Any, questions: Any, **_kwargs: Any) -> dict:
        try:
            stub.record("ask", state, questions)
        except FixtureNoResponse:
            raise route.client.JevError("offline_fixture_no_response") from None
        checked_questions = route.client.check_questions(questions)
        raw = {
            "difficulty": {"type": "score", "score": 0.04, "confidence": 0.98,
                           "probabilities": {"0": 0.97, "1": 0.02, "2": 0.01, "3": 0.0}},
            "kind": {"type": "choice", "choice": "general", "confidence": 0.98,
                     "probabilities": {"general": 0.97, "coding": 0.01,
                                       "writing": 0.01, "research": 0.01}},
            "costly_mistake": {"type": "noul", "noul": 0.01},
        }
        answers = {name: route.client._check_answer(name, question, raw[name])
                   for name, question in checked_questions.items()}
        return {"answers": answers, "latency_ms": 0, "usage": {},
                "jev_model": "offline-synthetic-fixture", "provider": "offline-fixture"}

    route.client.ask = ask
    ordered = sorted(models, key=lambda row: row["cost"])
    tiers: dict[str, dict[str, list[str]]] = {}
    for index, tier in enumerate(route.TIERS):
        pool = [provider + ":" + row["model"] for rank, row in enumerate(ordered)
                if min(2, rank * 3 // len(ordered)) == index]
        if not pool:
            pool = [provider + ":" + ordered[min(len(ordered) - 1, index * len(ordered) // 3)]["model"]]
        tiers[tier] = {specialty: list(pool) for specialty in route.SPECIALTIES}
    config = json.loads(json.dumps(route.DEFAULT_CONFIG))
    config.update({"cache_repeat_asks": False, "cache_size": 0, "exclude": [],
                   "private_profiles": [], "tiers": tiers,
                   "escalation": {"enabled": False, "rungs": []},
                   "ask_sections": [], "drop_sections": [],
                   "ask_chars": max(2500, len(task))})
    rows = [{"provider": provider, "model": row["model"], "price": row["cost"],
             "context": row["context_limit"], "vision": False, "released": ""}
            for row in models]
    decision = route.decide(task, current=current, only_provider=provider, pinned=pinned,
                            context_tokens=context_tokens, has_images=has_images,
                            config=config, rows=rows, timeout=0.1)
    # Jev's kept-current result is not a model-selection proposal. A selected
    # tier can legitimately choose current; retain that no-change recommendation.
    proposed = decision.get("model") if decision.get("tier") else None
    return decision, proposed, None if proposed is not None else decision.get("reason")


# Only routing facts, not arbitrary notices, paths, task echoes, or exception text.
_DECISION_FIELDS = frozenset({
    "schema_version", "status", "selected", "recommendation", "applied", "registry_version",
    "abstention_reason", "candidate_count", "eligible_count", "capability_fit_scores",
    "model", "latency_ms", "usage", "request_count", "routed", "provider", "model_id",
    "tier", "specialty", "has_images", "confidence", "difficulty", "costly_mistake",
    "private", "mode", "answers", "policy", "reason", "unwrapped", "effort_tier",
})


def _assert_not_applied(value: Any) -> None:
    if isinstance(value, dict):
        if value.get("applied") is True:
            raise RouteApplicationDenied()
        for child in value.values():
            _assert_not_applied(child)
    elif isinstance(value, list):
        for child in value:
            _assert_not_applied(child)


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError()
        out[key] = value
    return out


def _no_constant(_value: str) -> None:
    raise ValueError()


def main() -> int:
    sys.dont_write_bytecode = True
    calls: list[dict[str, Any]] = []
    denied = {"network": 0, "process": 0}
    _deny_external_operations(denied)
    result: dict[str, Any] = {
        "proposed_model": None, "abstained": True, "abstention_reason": None,
        "implementation_status": "worker_error", "simulated_requests": 0,
        "provider_requests": [], "raw_decision": {}, "task_received": None,
        "candidate_ids": [], "network_attempts": 0, "process_attempts": 0,
        "worker_failed": False, "loaded_source_root": "",
    }
    scenario = None
    raw_decision: dict[str, Any] = {}
    try:
        if len(sys.argv) != 1:
            raise ValueError()
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError()
        payload = json.loads(raw, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
        if not isinstance(payload, dict):
            raise ValueError()
        case = payload["case"]
        if not isinstance(case, dict) or type(case.get("task")) is not str:
            raise ValueError()
        task = case["task"]  # No strip, reformat, label injection, or substitution.
        result["task_received"] = task
        scenario = payload["scenario"]
        if scenario not in ("no-response", "synthetic-simple"):
            raise ValueError()
        provider, current, models, refs = _catalog(payload["catalog"])
        result["candidate_ids"] = refs
        context_tokens = case.get("context_tokens", 0)
        has_images = case.get("has_images", False)
        pinned = case.get("pinned", case.get("id") == "manual-model-pin")
        if (type(context_tokens) is not int or context_tokens < 0
                or type(has_images) is not bool or type(pinned) is not bool):
            raise ValueError()
        root = Path(_text(payload["source_root"])).resolve(strict=True)
        stub = FixtureClient(scenario, calls)
        # Source imports/calls may write diagnostics; stdout remains exactly one
        # JSON object, and discarded diagnostics cannot leak exception details.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            if payload["arm"] == "switchyard":
                raw_decision, proposed, reason = _switchyard(root, task, provider, models, context_tokens, stub)
            elif payload["arm"] == "jev-skills":
                raw_decision, proposed, reason = _jev(root, task, provider, current, models,
                                                     context_tokens, has_images, pinned, stub)
            else:
                raise ValueError()
        package = "hermes_switchyard" if payload["arm"] == "switchyard" else "jevkit"
        result["loaded_source_root"] = _verified_source_root(root, package)
        _assert_not_applied(raw_decision)
        if any(denied.values()):
            raise OfflineDenied()
        if proposed is not None and proposed not in refs:
            raise ValueError()
        result.update({"proposed_model": proposed, "abstained": proposed is None,
                       "abstention_reason": reason if proposed is None else None,
                       "implementation_status": "offline_no_response" if scenario == "no-response"
                       else "offline_synthetic_fixture"})
        result["raw_decision"] = {key: value for key, value in raw_decision.items() if key in _DECISION_FIELDS}
    except Exception as exc:
        result["abstention_reason"] = "worker_error"
        result["worker_failed"] = True
        result["raw_decision"] = {"error_type": type(exc).__name__}
    result["simulated_requests"] = len(calls)
    result["network_attempts"] = denied["network"]
    result["process_attempts"] = denied["process"]
    sanitized: dict[str, Any] = result["raw_decision"]
    sanitized.update({
        "provenance": {"scenario": scenario if scenario in ("no-response", "synthetic-simple") else None,
                       "synthetic": True, "live_inference": False, "route_applied": False},
        "stub_calls": calls, "denied_external_operations": sum(denied.values()),
    })
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
