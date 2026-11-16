"""Profile-owned approved model registry for explicit Jev recommendations."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from .routing import DEFAULT_MODEL_CAPABILITY_FIT_THRESHOLD, route_model

_ALLOWED_KEYS = frozenset({
    "id", "provider", "model", "account", "approved", "data_classes_allowed",
    "tool_capabilities", "context_limit", "cost", "description",
})


def _base(status: str, registry_version: str, reason: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": status,
        "selected": None,
        "recommendation": None,
        "applied": False,
        "registry_version": registry_version,
        "abstention_reason": reason,
        "selection_policy": "advisory recommendation only; active Hermes route is unchanged",
    }


def _parse_deadline(value: Any) -> datetime | None:
    if type(value) is not str or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _validate_registry(registry: Any) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    if not isinstance(registry, list) or not registry:
        raise ValueError("approved model registry must be a non-empty list")
    projected: list[dict[str, Any]] = []
    metadata: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    for item in registry:
        if not isinstance(item, dict) or set(item) - _ALLOWED_KEYS:
            raise ValueError("registry entry has unknown fields")
        identifier = item.get("id")
        provider = item.get("provider")
        model = item.get("model")
        account = item.get("account")
        if any(type(value) is not str or not value.strip() for value in (identifier, provider, model, account)):
            raise ValueError("registry identity fields must be non-empty strings")
        assert isinstance(identifier, str)
        if identifier in seen:
            raise ValueError("registry identifiers must be unique")
        seen.add(identifier)
        approved = item.get("approved")
        if type(approved) is not bool:
            raise ValueError("registry entries require an explicit approved boolean")
        description = item.get("description", "")
        if type(description) is not str:
            raise ValueError("registry descriptions must be strings")
        cost = item.get("cost")
        if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost < 0:
            raise ValueError("registry entries require a non-negative cost")
        candidate: dict[str, Any] = {
            "id": identifier,
            "description": description,
            "approved": approved,
            "cost": float(cost),
        }
        for key in ("data_classes_allowed", "tool_capabilities"):
            if key in item:
                values = item[key]
                if not isinstance(values, list) or any(type(v) is not str or not v for v in values):
                    raise ValueError(f"registry {key} must be a list of non-empty strings")
                if len(set(values)) != len(values):
                    raise ValueError(f"registry {key} must not contain duplicate values")
                candidate[key] = list(values)
        if "context_limit" in item:
            context_limit = item["context_limit"]
            if type(context_limit) is not int or context_limit <= 0 or isinstance(context_limit, bool):
                raise ValueError("registry context_limit must be a positive integer")
            candidate["context_limit"] = context_limit
        if not math.isfinite(float(candidate["cost"])):
            raise ValueError("registry cost must be finite")
        projected.append(candidate)
        metadata[identifier] = {
            "provider": str(provider),
            "model": str(model),
            "account": str(account),
        }
    if not any(candidate["approved"] for candidate in projected):
        raise ValueError("registry has no approved candidates")
    return projected, metadata


def recommend_approved_model(
    *,
    task: str,
    requirements: dict,
    registry: Any,
    registry_version: Any,
    valid_until: Any,
    client: Any,
    public_or_sanitized_data_ack: bool = False,
    capability_fit_threshold: float = DEFAULT_MODEL_CAPABILITY_FIT_THRESHOLD,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a typed recommendation without changing the active Hermes route."""
    version = registry_version if type(registry_version) is str else ""
    if not version.strip():
        return _base("invalid_registry", version, "registry_version_missing")
    deadline = _parse_deadline(valid_until)
    if deadline is None:
        return _base("invalid_registry", version, "registry_valid_until_invalid")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if deadline <= current:
        return _base("stale_registry", version, "registry_expired")
    try:
        candidates, metadata = _validate_registry(registry)
    except (TypeError, ValueError):
        return _base("invalid_registry", version, "registry_validation_failed")
    try:
        routed = route_model(
            task=task,
            candidates=candidates,
            requirements=requirements,
            client=client,
            capability_fit_threshold=capability_fit_threshold,
            public_or_sanitized_data_ack=public_or_sanitized_data_ack,
        )
    except RuntimeError:
        return _base("provider_unavailable", version, "jev_provider_unavailable")
    except (TypeError, ValueError):
        # A syntactically valid but malformed Jev response is a provider-response
        # failure, not a caller request error. Fail closed to the typed contract.
        return _base("provider_unavailable", version, "invalid_response")
    if routed.get("status") != "selected":
        eligible = routed.get("eligible_candidates") or []
        excluded = routed.get("excluded_candidates") or []
        reasons = {
            reason
            for entry in excluded if isinstance(entry, dict)
            for reason in entry.get("reasons", []) if isinstance(reason, str)
        }
        only_over_budget = bool(eligible) is False and reasons == {"over_budget"}
        status = "budget_exhausted" if only_over_budget else "abstained"
        result = _base(status, version, routed.get("abstention_reason") or "no_approved_route")
        result["candidate_count"] = len(candidates)
        result["eligible_count"] = len(eligible)
        return result
    selected = routed.get("selected")
    if selected not in metadata:
        return _base("invalid_response", version, "selected_identifier_not_in_registry")
    result = _base("selected", version)
    result.update({
        "selected": selected,
        "recommendation": {"id": selected, **metadata[selected]},
        "candidate_count": len(candidates),
        "eligible_count": len(routed.get("eligible_candidates") or []),
        "capability_fit_scores": routed.get("capability_fit_scores") or {},
        "model": routed.get("model"),
        "latency_ms": routed.get("latency_ms"),
        "usage": routed.get("usage") or {},
        "request_count": routed.get("request_count"),
        "request_id": routed.get("request_id"),
        "abstention_reason": None,
    })
    return result
