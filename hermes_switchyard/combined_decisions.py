"""Opt-in sharing of a single validated Jev decision across skill and effort.

Default-off, production-contained integration. The existing skills hook retains
its normal consumer and egress policy. Only clean foreground CLI first turns,
a direct single-provider skill request, and an exact later effort request share.
All other cases follow the unchanged independent decision paths.
"""
from __future__ import annotations

import contextvars
import copy
import functools
import hashlib
import json
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from typing import Any

from .reasoning_effort_adapter import _task_scan, choose_reasoning_effort

_SCOPE = contextvars.ContextVar("switchyard_shared_skill_scope", default=None)
_MAX_PENDING = 32
_MAX_AGE_SECONDS = 30.0


class _Capture:
    def decide(self, state, questions, **_kwargs):
        self.parts = copy.deepcopy((state, questions))
        raise RuntimeError("capture only")


class _Replay:
    def __init__(self, result):
        self.result = result

    def decide(self, _state, questions, **_kwargs):
        return {
            **self.result,
            "answers": {key: self.result["answers"][key] for key in questions},
        }


def _effort_parts(task: str, cap: str):
    capture = _Capture()
    options = ["low", "medium"] if cap == "medium" else ["low", "medium", "high"]
    choose_reasoning_effort(
        task=task,
        recent_tool_outcomes=[],
        client=capture,
        requested_effort=cap,
        allowed_efforts=options,
        deadline_seconds=0.4,
    )
    return capture.parts


class SharedDecisionBroker:
    """Bounded consume-once cache. Records never leave this Python process."""

    def __init__(self, *, identity_factory: Callable[[], Any]):
        self.identity_factory = identity_factory
        self.records: OrderedDict[tuple[str, str, str], dict] = OrderedDict()
        self.receipts = deque(maxlen=_MAX_PENDING)
        self.lock = threading.Lock()

    def _route_identity(self) -> str | None:
        try:
            identity = self.identity_factory()
            if not isinstance(identity, Mapping):
                return None
            raw = json.dumps(identity, sort_keys=True, allow_nan=False)
            return hashlib.sha256(raw.encode("utf-8")).hexdigest()
        except Exception:
            return None

    def clear(self, key):
        with self.lock:
            self.records.pop(key, None)

    def store(self, key, task, response, elapsed_ms):
        identity = self._route_identity()
        if (
            identity is None or not isinstance(key, tuple) or len(key) != 3
            or any(not isinstance(item, str) or not item for item in key)
            or not isinstance(task, str)
            or not isinstance(response, Mapping)
            or response.get("request_count") != 1
            or not isinstance(response.get("request_id"), str)
            or not response["request_id"].strip()
            or not isinstance(response.get("answers"), Mapping)
        ):
            return
        record = {
            "task": task,
            "result": copy.deepcopy(dict(response)),
            "identity": identity,
            "at": time.monotonic(),
            "wall_ms": elapsed_ms,
        }
        with self.lock:
            self.records.pop(key, None)
            self.records[key] = record
            while len(self.records) > _MAX_PENDING:
                self.records.popitem(last=False)

    def consume(self, key, capture, requested, candidates, controller):
        if (
            not isinstance(key, tuple)
            or len(key) != 3
            or any(not isinstance(item, str) or not item for item in key)
            or not isinstance(capture, Mapping)
            or capture.get("scan_reason")
            or capture.get("metadata") is not None
            or controller.public_or_sanitized_data_ack is not True
            or list(candidates) != {
                "medium": ["low", "medium"],
                "high": ["low", "medium", "high"],
            }.get(requested)
        ):
            return None
        with self.lock:
            record = self.records.pop(key, None)
        if record is None:
            return None
        identity = self._route_identity()
        if (
            identity is None or identity != record["identity"]
            or capture.get("excerpt") != record["task"]
            or record["wall_ms"] > controller.deadline_seconds * 1000
            or time.monotonic() - record["at"] > _MAX_AGE_SECONDS
        ):
            return None
        result = copy.deepcopy(record["result"])
        answers = result.get("answers")
        if not isinstance(answers, Mapping):
            return None
        effort_key = "reasoning_effort_" + requested
        if effort_key not in answers or "stakes" not in answers:
            return None
        result["answers"]["reasoning_effort"] = answers[effort_key]
        choice = choose_reasoning_effort(
            task=record["task"],
            recent_tool_outcomes=[],
            client=_Replay(result),
            requested_effort=requested,
            allowed_efforts=candidates,
            public_or_sanitized_data_ack=True,
            deadline_seconds=controller.deadline_seconds,
        )
        if choice.get("status") != "selected":
            return None
        with self.lock:
            self.receipts.append({
                "shared_request_id": result["request_id"],
                "cap": requested,
                "effort": choice["effort"],
                "shared_latency_ms": record["wall_ms"],
            })
        # The skill route already owns the physical request and its usage.
        return {**choice, "jev_called": False, "shared_decision": True}


class SharedSkillClient:
    """Per-skill-client facade; never replaces the process-wide DecisionClient."""

    def __init__(self, client: Any, broker: SharedDecisionBroker):
        self._client = client
        self._broker = broker

    def __getattr__(self, name):
        return getattr(self._client, name)

    def decide(self, state, questions, **kwargs):
        scope = _SCOPE.get()
        if not (
            isinstance(scope, Mapping)
            and isinstance(state, dict)
            and state.get("stage") != 2
            and state.get("task") == scope.get("task")
            and isinstance(questions, dict)
            and set(questions) == {"skill", "needs_skill"}
        ):
            return self._client.decide(state, questions, **kwargs)
        try:
            merged = copy.deepcopy(questions)
            merged_state = copy.deepcopy(state)
            for cap in ("medium", "high"):
                effort_state, effort_questions = _effort_parts(scope["task"], cap)
                merged_state.update({
                    key: value for key, value in effort_state.items()
                    if key != "current_request"
                })
                entry = copy.deepcopy(effort_questions["reasoning_effort"])
                entry["instructions"] = entry["instructions"].replace(
                    "current_request", "task"
                )
                merged["reasoning_effort_" + cap] = entry
            stakes = copy.deepcopy(effort_questions["stakes"])
            stakes["instructions"] = stakes["instructions"].replace(
                "current_request", "task"
            )
            merged["stakes"] = stakes
            # A split into multiple physical requests is not consolidation.
            if len(self._client._question_batches(merged_state, merged)) != 1:
                return self._client.decide(state, questions, **kwargs)
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError):
            return self._client.decide(state, questions, **kwargs)
        started = time.perf_counter()
        result = self._client.decide(merged_state, merged, **kwargs)
        elapsed_ms = (time.perf_counter() - started) * 1000
        self._broker.store(scope["key"], scope["task"], result, elapsed_ms)
        return {
            **result,
            "answers": {key: result["answers"][key] for key in questions},
        }


def wrap_skill_pre_hook(callback, broker: SharedDecisionBroker):
    """Enter shared scope only while the existing automatic hook runs."""

    @functools.wraps(callback)
    def wrapped(**context):
        key = tuple(context.get(item) for item in ("session_id", "task_id", "turn_id"))
        valid_key = all(isinstance(item, str) and item.strip() for item in key)
        if valid_key:
            broker.clear(key)
        message = context.get("user_message")
        clean, reason = _task_scan(message)
        eligible = (
            valid_key
            and isinstance(message, str)
            and len(message) <= 1200
            and reason is None
            and clean == message
            and context.get("platform") == "cli"
            and not context.get("parent_session_id")
            and context.get("model") == "gpt-6-sol"
        )
        token = _SCOPE.set({
            "key": key, "task": clean
        } if eligible else None)
        try:
            return callback(**context)
        finally:
            _SCOPE.reset(token)
            # Keep existing observer/status access to the original callback.
            for name in ("last_result", "last_receipt", "last_metadata", "last_routing_metadata"):
                if hasattr(callback, name):
                    setattr(wrapped, name, getattr(callback, name))

    return wrapped
