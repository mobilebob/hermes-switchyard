"""Passive consumer of explicit approved-registry model decisions.

No client, provider call, model apply callback, or hook registration lives here.
Only the approved-model tool owner may offer a completed decision. Request
observation is called by the existing effort middleware. Pin/account fields
must come from trusted native request context, never tool arguments or prose.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import threading
import time
from typing import Any, Callable

from .model_policy import _parse_deadline, _validate_registry
from .native_provenance import NativeProvenanceAdapter
from .reasoning_effort_adapter import _task_scan


class ModelRoutingShadow:
    LIMIT = 32
    MAX_AGE = 30.0
    DEADLINE = 0.4

    def __init__(self, *, mode: Any, identity_factory: Callable[[], Any], registry_factory: Callable[[], Any], native_provenance=None):
        self.mode = mode if mode in ("off", "shadow", "apply") else "off"
        self.native_provenance = native_provenance or NativeProvenanceAdapter()
        self.identity_factory: Callable[[], Any] = identity_factory
        self.registry_factory: Callable[[], Any] = registry_factory
        self.captures = OrderedDict()
        self.pending = OrderedDict()
        self.receipts = deque(maxlen=self.LIMIT)
        self.lock = threading.RLock()
        self.status = {"mode": self.mode, "apply_available": False, "last_receipt": None}

    def key(self, context):
        key = tuple(context.get(name) for name in ("session_id", "task_id", "turn_id"))
        if not all(type(value) is str and value.strip() for value in key):
            return None
        profile = self.native_provenance.profile()
        return ((profile[0] if profile else None), *key)

    @staticmethod
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def identity(self):
        try:
            route = self.identity_factory()
            return self.digest((route, self.native_provenance.profile())) if isinstance(route, Mapping) else None
        except Exception:
            return None

    def registry(self):
        try:
            rows, version, expiry = self.registry_factory()
            deadline = _parse_deadline(expiry)
            if type(version) is not str or not version.strip() or deadline is None:
                return None
            if deadline <= datetime.now(timezone.utc):
                return None
            _, metadata = _validate_registry(rows)
            approved = {row["id"]: metadata[row["id"]] for row in rows if row["approved"] is True}
            return self.digest((rows, version, expiry)), version, approved
        except Exception:
            return None

    def safely(self, operation, *args, **kwargs):
        """An optional observer failure must not break its existing policy owner."""
        try:
            return getattr(self, operation)(*args, **kwargs)
        except Exception:
            self.invalidate()
            with self.lock:
                receipt = {"mode": self.mode, "status": "abstained", "reason": "observer_failed",
                           "proposed": None, "applied": False, "can_apply": False,
                           "new_jev_requests": 0, "scope_sha256": None}
                self.receipts.append(receipt)
                self.status["last_receipt"] = receipt
            return None

    def invalidate(self, **_context):
        with self.lock:
            self.captures.clear()
            self.pending.clear()
            self.native_provenance.invalidate()

    def capture(self, *, user_message=None, **context):
        if self.mode != "shadow":
            return
        key = self.key(context)
        if key is None:
            return
        clean, reason = _task_scan(user_message)
        with self.lock:
            # Each new capture supersedes in-flight offers, including same turn.
            for previous in list(self.captures):
                if previous[:-1] == key[:-1]:
                    self.captures.pop(previous, None)
                    self.pending.pop(previous, None)
            if (context.get("parent_session_id") not in (None, "")
                    or context.get("platform") != "cli" or reason is not None
                    or type(user_message) is not str or len(user_message) > 1200
                    or clean != user_message):
                return
            self.captures[key] = (object(), self.digest(clean))
            while len(self.captures) > self.LIMIT:
                old, _ = self.captures.popitem(last=False)
                self.pending.pop(old, None)

    def begin(self, *, task, context, consent):
        """Start provenance before an already requested approved-model operation."""
        if self.mode != "shadow" or consent is not True:
            return None
        key = self.key(context)
        clean, reason = _task_scan(task)
        registry, identity = self.registry(), self.identity()
        if key is None or reason is not None or clean != task or registry is None or identity is None:
            return None
        with self.lock:
            capture = self.captures.get(key)
            if capture is None or capture[1] != self.digest(clean):
                return None
            ticket = {"key": key, "generation": capture[0], "task": capture[1],
                      "registry": registry[0], "version": registry[1],
                      "identity": identity, "started": time.monotonic()}
            self.pending[key] = ticket
            return ticket

    def offer(self, ticket, result):
        """Only typed, successful model-selection receipts qualify, never effort."""
        if ticket is None or not isinstance(result, Mapping):
            return
        registry, identity = self.registry(), self.identity()
        recommendation = result.get("recommendation")
        request_id = result.get("request_id")
        if (result.get("status") != "selected" or result.get("applied") is not False
                or type(result.get("request_count")) is not int or result["request_count"] != 1 or type(request_id) is not str or not request_id.strip()
                or result.get("registry_version") != ticket["version"]
                or not isinstance(recommendation, Mapping) or registry is None
                or registry[0] != ticket["registry"] or identity != ticket["identity"]
                or time.monotonic() - ticket["started"] > self.DEADLINE):
            return
        selected = result.get("selected")
        expected = registry[2].get(selected) if type(selected) is str else None
        if expected is None or dict(recommendation) != {"id": selected, **expected}:
            return
        with self.lock:
            capture = self.captures.get(ticket["key"])
            if (self.pending.get(ticket["key"]) is not ticket or capture is None
                    or capture[0] is not ticket["generation"]):
                return
            ticket["decision"] = {"id": selected, **expected, "request_id": request_id}
            ticket["completed"] = time.monotonic()

    def observe(self, request, *, consent, **context):
        """Record only; intentionally never accepts a request mutation callback."""
        if self.mode == "off":
            return
        native = self.native_provenance.read(request, context)
        context = native.context
        key = self.key(context)
        with self.lock:
            ticket = self.pending.pop(key, None)
            capture = self.captures.get(key)
            reason = "decision_unavailable"
            proposed = None
            if self.mode == "apply":
                reason = "apply_unavailable"
            elif consent is not True:
                reason = "consent_required"
            elif native.reason is not None:
                reason = native.reason
            elif context.get("manual_model_pinned") is True:
                reason = "manual_model_pin"
            elif context.get("manual_model_pinned") is not False:
                reason = "pin_provenance_unavailable"
            elif key is None or capture is None:
                reason = "capture_unavailable"
            elif (request.get("tools") or request.get("previous_response_id")
                  or not isinstance(request.get("input"), list) or len(request["input"]) != 1
                  or not isinstance(request["input"][0], Mapping)
                  or request["input"][0].get("role") != "user"
                  or type(request["input"][0].get("content")) is not str
                  or len(request["input"][0]["content"]) > 1200
                  or self.digest(request["input"][0]["content"]) != capture[1]):
                reason = "request_task_mismatch_or_unsupported"
            elif ticket is not None and "decision" in ticket:
                registry, identity = self.registry(), self.identity()
                choice = ticket["decision"]
                if capture[0] is not ticket["generation"]:
                    reason = "cancelled_or_superseded"
                elif registry is None or registry[0] != ticket["registry"]:
                    reason = "registry_changed"
                elif identity is None or identity != ticket["identity"]:
                    reason = "identity_changed"
                elif time.monotonic() - ticket["completed"] > self.MAX_AGE:
                    reason = "expired"
                elif not context.get("provider") or choice["provider"] != context["provider"]:
                    reason = "provider_mismatch"
                elif not context.get("model_account") or choice["account"] != context["model_account"]:
                    reason = "account_mismatch_or_unknown"
                elif not context.get("model") or request.get("model") != context["model"]:
                    reason = "active_model_mismatch"
                else:
                    proposed = {name: choice[name] for name in ("id", "provider", "model", "account")}
                    reason = "same_provider_proposal"
            self.receipts.append({
                "mode": self.mode, "status": "proposed" if proposed else "abstained",
                "reason": reason, "proposed": proposed, "applied": False,
                "can_apply": False, "new_jev_requests": 0,
                "scope_sha256": self.digest(key) if key else None,
                "native_provenance": native.receipt(),
            })
            self.status["last_receipt"] = self.receipts[-1]
