"""Read-only native provenance for passive routing; no route/profile management.

The inspected Hermes request builder emits model/provider/endpoint/scope/attempt,
not manual-pin or credential/account identity. Extra kwargs cannot fill those
missing authorities. Never inspect stacks, agent private state, auth stores,
legacy auxiliary globals, or re-resolve a provider's credentials here.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import threading
from types import MappingProxyType
from typing import Any


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def native_profile():
    """Delegate all effective-home/profile resolution to Hermes' public owner."""
    try:
        from hermes_constants import get_hermes_home, hermes_home_key, profile_name_for_home
        home = get_hermes_home()
        name = profile_name_for_home(home)
        if type(name) is not str or not name:
            return None
        return _digest(hermes_home_key(home)), name
    except (ImportError, AttributeError, OSError, RuntimeError, ValueError):
        return None


@dataclass(frozen=True)
class NativeSnapshot:
    context: Mapping[str, Any]
    profile: tuple[str, str] | None
    reason: str | None
    route_sha256: str | None
    source: str = "hermes-request-context"
    missing: tuple[str, ...] = ("manual_model_pin", "provider_account")

    def receipt(self):
        return {"source": self.source, "reason": self.reason, "missing": list(self.missing),
                "profile": self.profile[1] if self.profile else None,
                "home_sha256": self.profile[0] if self.profile else None,
                "route_sha256": self.route_sha256}


class NativeProvenanceAdapter:
    LIMIT = 128
    # Exactly the fields emitted at agent.turn_api_request.build_api_request.
    FIELDS = ("session_id", "task_id", "turn_id", "api_request_id", "platform",
              "model", "provider", "base_url", "api_mode", "api_call_count")

    def __init__(self):
        self.lock = threading.Lock()
        self.seen = OrderedDict()
        self.revoked = OrderedDict()

    def profile(self):
        return native_profile()

    def invalidate(self):
        with self.lock:
            for key in self.seen:
                self.revoked[key] = True
            self.seen.clear()
            while len(self.revoked) > self.LIMIT:
                self.revoked.popitem(last=False)

    def read(self, request, context):
        # This mapping is fed only to the observer, never back into the request
        # owner. The owner's original context and payload remain untouched.
        clean = {key: context.get(key) for key in self.FIELDS}
        clean["manual_model_pinned"] = None
        clean["model_account"] = None
        profile = self.profile()
        reason = None
        required = ("session_id", "task_id", "turn_id", "api_request_id", "model", "provider", "base_url", "api_mode")
        if profile is None:
            reason = "native_profile_unavailable"
        elif any(type(clean[name]) is not str or not clean[name].strip() for name in required):
            reason = "native_request_provenance_incomplete"
        elif not isinstance(request, Mapping) or request.get("model") != clean["model"]:
            reason = "native_model_conflict"
        route = None
        if reason is None:
            assert profile is not None
            route = _digest((profile, *(clean[name] for name in ("model", "provider", "base_url", "api_mode"))))
            scope = tuple(clean[name] for name in ("session_id", "task_id", "turn_id"))
            key = (profile[0], clean["api_request_id"])
            identity = (scope, route)
            with self.lock:
                if key in self.revoked:
                    reason = "native_attempt_cancelled"
                elif key in self.seen and self.seen[key] != identity:
                    reason = "native_attempt_conflict"
                else:
                    self.seen[key] = identity
                    self.seen.move_to_end(key)
                    while len(self.seen) > self.LIMIT:
                        self.seen.popitem(last=False)
        return NativeSnapshot(MappingProxyType(clean), profile, reason, route)
