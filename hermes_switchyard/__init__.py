"""Jev structured-decision plugin for Hermes."""

from __future__ import annotations

import json
import hashlib
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import receipt_history, receipt_state, schemas
from .automatic import _config_float, build_pre_llm_call_hook, discover_mandatory_skills
from .client import (
    DEFAULT_AUTOMATIC_ROUTING_DEADLINE_SECONDS,
    DEFAULT_ENDPOINT,
    DEFAULT_OPERATION_DEADLINE_SECONDS,
    MAX_DECISION_REQUESTS,
    TYPESAFE_ENDPOINT,
    DecisionClient,
    PartialAccountingError,
    request_budget_scope,
)
from . import browser_use, legacy_cleanup
from .computer_use import StaleTargetError, run_computer_goal
from .egress import (
    DEFAULT_AUTOMATIC_PUBLIC_OR_SANITIZED_DATA_ACK,
    DEFAULT_CONSUMER_MODE,
    DEFAULT_ROUTING_MODE,
    is_routing_mode,
)
from .model_policy import recommend_approved_model
from .model_route_adapter import register_model_route_adapter
from .reasoning_effort_adapter import (
    DEFAULT_ADAPTIVE_REASONING_DEADLINE_SECONDS,
    append_effort_record,
    effort_stats,
    register_reasoning_effort_adapter,
)
from .routing import route_model, select_skill, select_skills
from .session_search_rerank import rerank_session_search
from .defer_tool_schemas import register_defer_tool_schemas_middleware
from .two_stage_routing import TWO_STAGE_CONFIG_KEYS, TwoStageConfig, skill_excerpt

from .host_compat import ctx_get_config, register_auxiliary_task as register_host_auxiliary_task


_UNREGISTERED_RUNTIME_STATUS = {
    "plugin_loaded": False,
    "routing_mode": None,
    "consumer_mode": None,
    "public_or_sanitized_data_ack": None,
    "automatic_skill_jev_mode": None,
    "hosted_construction_allowed": False,
    "model_route_adapter": None,
    "reasoning_effort_adapter": None,
    "defer_tool_schemas": None,
}
_RUNTIME_STATUS = dict(_UNREGISTERED_RUNTIME_STATUS)

# A session exposes a tool only when the toolset it is registered under is selected for that
# session. jev_computer_use rides Hermes' computer_use toolset; every other tool lives on the
# plugin's own toolset. A toolset pin that omits either one drops that group of tools.
COMPUTER_USE_TOOLSET = "computer_use"
PLUGIN_TOOLSET = "hermes_switchyard"
TOOL_TOOLSETS = {
    "jev_assess": PLUGIN_TOOLSET,
    "jev_computer_use": COMPUTER_USE_TOOLSET,
    "jev_skill_select": PLUGIN_TOOLSET,
    "jev_skill_select_many": PLUGIN_TOOLSET,
    "jev_model_route": PLUGIN_TOOLSET,
    "jev_model_route_approved": PLUGIN_TOOLSET,
    "jev_session_search_rerank": PLUGIN_TOOLSET,
}
# Sessions that advertise Switchyard computer use need both toolsets selected.
# Plugin Doctor / plugin-enable only toggles one plugin toolset key
# (hermes_switchyard); computer_use must also be selected for jev_computer_use.
REQUIRED_SESSION_TOOLSETS = (COMPUTER_USE_TOOLSET, PLUGIN_TOOLSET)

# Handlers this process passed to ctx.register_tool, by tool name. Comparing them with the
# Hermes registry separates "this plugin called register_tool" from "Hermes holds this
# plugin's registration": the registry rejects a name another registration already owns
# under a different toolset without raising.
_REGISTERED_HANDLERS: dict[str, Any] = {}

# Resolves the provider the configured route uses, so status can check that provider's key
# instead of any key. Set by register(); absent in a process where register() never ran.
_ROUTE_STATUS: dict[str, Any] = {}

# Providers a Jev route can resolve to, each with its own credential.
PROVIDERS = ("typesafe", "openrouter")


def reset_runtime_status() -> None:
    """Clear register-time status. Tests use this to model a fresh process."""
    _RUNTIME_STATUS.clear()
    _RUNTIME_STATUS.update(_UNREGISTERED_RUNTIME_STATUS)
    _REGISTERED_HANDLERS.clear()
    _ROUTE_STATUS.clear()


def _publish_runtime_status(
    *,
    routing_mode: Any,
    consumer_mode: Any,
    public_or_sanitized_data_ack: bool,
    automatic_skill_jev_mode: Any,
) -> None:
    ack = public_or_sanitized_data_ack is True
    mode = routing_mode if is_routing_mode(routing_mode) else None
    _RUNTIME_STATUS.update(
        {
            "plugin_loaded": True,
            "routing_mode": mode,
            "consumer_mode": consumer_mode if consumer_mode in {"advisory", "load"} else None,
            "public_or_sanitized_data_ack": ack,
            "automatic_skill_jev_mode": (
                automatic_skill_jev_mode if automatic_skill_jev_mode in {"always", "uncertain_only"} else None
            ),
            "hosted_construction_allowed": bool(mode == "hosted_sanitized" and ack),
            "model_route_adapter": _RUNTIME_STATUS.get("model_route_adapter"),
            "reasoning_effort_adapter": _RUNTIME_STATUS.get("reasoning_effort_adapter"),
            "defer_tool_schemas": _RUNTIME_STATUS.get("defer_tool_schemas"),
        }
    )


def _after_install_text() -> str:
    """Return the install-time next-step text without network access."""
    path = Path(__file__).resolve().parent.parent / "after-install.md"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    if text:
        return text
    return (
        "Run hermes switchyard setup --provider typesafe "
        "(or --provider openrouter), then start a fresh session."
    )


def _plugin_version() -> str | None:
    """Return the version this copy of the plugin declares, or None when unreadable."""
    path = Path(__file__).resolve().parent.parent / "plugin.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^version:\s*([^\s#]+)", text, flags=re.MULTILINE)
    return match.group(1).strip("\"'") if match else None


def _load_hermes_seams() -> SimpleNamespace:
    """Load the Hermes entry points the exposure report reads.

    Each seam loads on its own, so a Hermes that moved one of them still yields whatever
    evidence the others provide. A missing seam is reported, never guessed.
    """
    seams = SimpleNamespace(
        registry=None,
        get_tool_definitions=None,
        resolve_toolset=None,
        validate_toolset=None,
        default_selection=None,
        disabled_toolsets=None,
    )
    try:
        from tools.registry import registry

        seams.registry = registry
    except Exception:  # noqa: BLE001 -- absent or incompatible Hermes runtime
        pass
    try:
        import model_tools

        seams.get_tool_definitions = model_tools.get_tool_definitions
    except Exception:  # noqa: BLE001
        pass
    try:
        import toolsets

        seams.resolve_toolset = toolsets.resolve_toolset
        seams.validate_toolset = toolsets.validate_toolset
    except Exception:  # noqa: BLE001
        pass

    def default_selection():
        """Return the toolsets Hermes' CLI gives a session started without --toolsets."""
        from hermes_cli.config import load_config

        config = load_config()
        try:
            from agent.coding_context import coding_selection

            posture = coding_selection(platform="cli", config=config)
        except Exception:  # noqa: BLE001 -- the coding posture is optional in Hermes
            posture = None
        if posture:
            return "coding_posture", [str(name) for name in posture]
        from hermes_cli.tools_config import _get_platform_tools

        return "platform_default", sorted(str(name) for name in _get_platform_tools(config, "cli"))

    def disabled_toolsets():
        """Return agent.disabled_toolsets, which Hermes' CLI applies to every session it starts."""
        from hermes_cli.config import load_config

        agent_config = (load_config() or {}).get("agent") or {}
        raw = agent_config.get("disabled_toolsets")
        try:
            from agent.skill_utils import parse_config_string_list
        except ImportError:
            # Hermes 0.19.0 (and hosts without parse_config_string_list): accept list or CSV.
            if raw is None:
                names = []
            elif isinstance(raw, str):
                names = [part.strip() for part in raw.split(",") if part.strip()]
            elif isinstance(raw, (list, tuple, set)):
                names = [str(item).strip() for item in raw if str(item).strip()]
            else:
                names = []
        else:
            # Parser is present: let failures propagate so selection stays fail-closed.
            names = parse_config_string_list(raw)
        return [str(name).strip() for name in names if str(name).strip()]

    seams.default_selection = default_selection
    seams.disabled_toolsets = disabled_toolsets
    return seams


def _resolve_selection(seams: SimpleNamespace, requested: Any) -> dict[str, Any]:
    """Return the toolset selection to evaluate: an explicit pin, else Hermes' CLI default.

    Hermes subtracts the configured agent.disabled_toolsets from every CLI session, including
    one with an explicit pin, so that list is part of the selection.
    """
    text = str(requested).strip() if requested is not None else ""
    if text:
        source = "explicit_toolsets"
        enabled = [part.strip() for part in text.split(",") if part.strip()]
    else:
        source, enabled = seams.default_selection()
    disabled_source = getattr(seams, "disabled_toolsets", None)
    disabled = list(disabled_source()) if disabled_source is not None else []
    unknown: list[str] = []
    if seams.validate_toolset is not None:
        unknown = [name for name in enabled if not seams.validate_toolset(name)]
    return {
        "source": source,
        "enabled_toolsets": enabled,
        "disabled_toolsets": disabled,
        "unknown_toolsets": unknown,
    }


def _catalog_names(seams: SimpleNamespace, enabled_toolsets: list[str], disabled_toolsets: list[str]) -> set[str]:
    """Return the tool names in the catalog Hermes builds for a session with these toolsets.

    This is the un-deferred catalog that Tool Search's tool_describe and tool_call check
    against, so a name missing here is "not found in the session's callable catalog".
    """
    arguments = {
        "enabled_toolsets": list(enabled_toolsets),
        "disabled_toolsets": list(disabled_toolsets) or None,
        "quiet_mode": True,
    }
    try:
        definitions = seams.get_tool_definitions(skip_tool_search_assembly=True, **arguments)
    except TypeError:  # a Hermes without Tool Search has no deferred catalog to skip
        definitions = seams.get_tool_definitions(**arguments)
    names: set[str] = set()
    for definition in definitions or []:
        function = definition.get("function") if isinstance(definition, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str):
            names.add(name)
    return names


def _selection_reaches(seams: SimpleNamespace, enabled_toolsets: list[str], tool_name: str) -> bool | None:
    """Return whether a selected toolset resolves to the tool; None when Hermes cannot say."""
    if seams.resolve_toolset is None:
        return None
    for toolset in enabled_toolsets:
        try:
            if tool_name in seams.resolve_toolset(toolset):
                return True
        except Exception:  # noqa: BLE001 -- a name Hermes cannot resolve reaches nothing
            continue
    return False


def _blank_tool_state(expected_toolset: str) -> dict[str, Any]:
    return {
        "expected_toolset": expected_toolset,
        "registered": None,
        "registry_toolset": None,
        "callable": None,
        "reason": None,
    }


def _unavailable_exposure(reason: str) -> dict[str, Any]:
    return {
        "evidence": "unavailable",
        "unavailable_reason": reason,
        "selection": None,
        "tools": {name: _blank_tool_state(toolset) for name, toolset in TOOL_TOOLSETS.items()},
    }


def _exposure_failure_reason(
    seams: SimpleNamespace, entry: Any, selection: dict[str, Any], tool_name: str
) -> str:
    """Explain why a tool this plugin registered is absent from the session catalog."""
    # Suppression is checked first: adding the toolset to --toolsets cannot help while
    # agent.disabled_toolsets names it, because Hermes subtracts that list last.
    if _selection_reaches(seams, selection["disabled_toolsets"], tool_name) is True:
        return "toolset_disabled"
    if _selection_reaches(seams, selection["enabled_toolsets"], tool_name) is False:
        return "toolset_not_selected"
    check = getattr(entry, "check_fn", None)
    if check is not None:
        try:
            available = bool(check())
        except Exception:  # noqa: BLE001 -- Hermes hides a tool whose check raises
            available = False
        if not available:
            return "availability_check_failed"
    return "not_in_catalog"


def _toolset_composition() -> dict[str, Any]:
    """Describe required toolset composition and Doctor vs callable boundaries."""
    return {
        "required_for_full_surface": list(REQUIRED_SESSION_TOOLSETS),
        "jev_computer_use_requires": [COMPUTER_USE_TOOLSET],
        "decision_tools_require": [PLUGIN_TOOLSET],
        "plugin_doctor": (
            "Hermes Plugin Doctor reports discovery, import, and registration only; "
            "it does not evaluate per-session callable exposure. Use "
            "`hermes switchyard status --json` (optionally with --toolsets) for that."
        ),
        "windows_pin_example": (
            'powershell: hermes -t "computer_use,hermes_switchyard" chat'
        ),
        "coding_focus_note": (
            "When agent.coding_context is focus, no-pin CLI sessions use coding_selection "
            "before platform_toolsets; pin --toolsets or leave focus mode after ensure-toolsets."
        ),
    }


def _coding_focus_override(config: dict[str, Any]) -> dict[str, Any]:
    """Report when coding focus posture overrides platform_toolsets for no-pin CLI sessions."""
    try:
        from agent.coding_context import coding_selection

        posture = coding_selection(platform="cli", config=config)
    except Exception:  # noqa: BLE001 -- coding posture is optional in Hermes
        posture = None
    if not posture:
        return {
            "active": False,
            "source": None,
            "selected": [],
            "note": None,
        }
    return {
        "active": True,
        "source": "coding_posture",
        "selected": [str(name) for name in posture],
        "note": (
            "agent.coding_context focus selects toolsets before platform_toolsets for "
            "no-pin CLI sessions; pin --toolsets or leave focus mode so "
            "platform_toolsets.cli (including ensure-toolsets) applies."
        ),
    }


def _platform_default_toolsets(config: dict[str, Any], platform: str) -> list[str] | None:
    """Return Hermes' composite default toolsets for a platform, or None when unavailable.

    Probe without agent.disabled_toolsets so a temporary suppression is not baked into the
    saved platform list; Hermes continues to apply suppressions at session time.
    """
    try:
        from hermes_cli.tools_config import _get_platform_tools
    except Exception:  # noqa: BLE001
        return None
    probe = dict(config)
    existing = config.get("platform_toolsets")
    if isinstance(existing, dict):
        probe_platforms = dict(existing)
        probe_platforms.pop(platform, None)
        probe["platform_toolsets"] = probe_platforms
    agent = probe.get("agent")
    if isinstance(agent, dict):
        probe_agent = dict(agent)
        probe_agent.pop("disabled_toolsets", None)
        probe["agent"] = probe_agent
    try:
        names = _get_platform_tools(probe, platform)
    except Exception:  # noqa: BLE001
        return None
    return [str(name) for name in names]


def _disabled_toolset_names(config: dict[str, Any]) -> list[str] | None:
    """Return agent.disabled_toolsets as strings, or None when the value is unreadable."""
    agent = config.get("agent")
    if not isinstance(agent, dict):
        return []
    raw = agent.get("disabled_toolsets")
    if raw is None:
        return []
    try:
        from agent.skill_utils import parse_config_string_list
    except ImportError:
        if isinstance(raw, str):
            return [part.strip() for part in raw.split(",") if part.strip()]
        if isinstance(raw, (list, tuple, set)):
            return [str(item).strip() for item in raw if str(item).strip()]
        return None
    try:
        return [str(item).strip() for item in parse_config_string_list(raw) if str(item).strip()]
    except Exception:  # noqa: BLE001
        return None


def _required_toolset_suppression(
    config: dict[str, Any], toolsets: tuple[str, ...]
) -> dict[str, list[str]] | None:
    """Return required toolsets currently suppressed and the disabling entries causing it."""
    disabled = _disabled_toolset_names(config)
    if disabled is None:
        return None
    try:
        from toolsets import resolve_toolset
    except Exception:  # noqa: BLE001 -- unavailable in some Hermes builds
        resolve_toolset = None

    required_tools: dict[str, set[str]] = {}
    for toolset in toolsets:
        names = {name for name, expected in TOOL_TOOLSETS.items() if expected == toolset}
        if resolve_toolset is not None:
            try:
                names.update(str(name) for name in resolve_toolset(toolset))
            except Exception:  # noqa: BLE001
                pass
        required_tools[toolset] = names

    suppressed: list[str] = []
    suppressors: list[str] = []
    for entry in disabled:
        name = str(entry).strip()
        if not name:
            continue
        hits: set[str] = set()
        if name in set(toolsets):
            hits.add(name)
        elif name in {'all', '*'}:
            hits.update(toolsets)
        for toolset in toolsets:
            required = required_tools.get(toolset, set())
            if name in required:
                hits.add(toolset)
        if not hits and resolve_toolset is not None:
            try:
                resolved = {str(value) for value in resolve_toolset(name)}
            except Exception:  # noqa: BLE001
                resolved = set()
            for toolset in toolsets:
                required = required_tools.get(toolset, set())
                if required and resolved.intersection(required):
                    hits.add(toolset)
        if hits:
            suppressors.append(name)
            for toolset in toolsets:
                if toolset in hits and toolset not in suppressed:
                    suppressed.append(toolset)

    return {
        'disabled': [str(name).strip() for name in disabled if str(name).strip()],
        'suppressed': suppressed,
        'suppressors': suppressors,
    }


def _ensure_failure(
    reason: str,
    *,
    detail: str | None = None,
    added: list[str] | None = None,
    already_present: list[str] | None = None,
    focus_override: dict[str, Any] | None = None,
    suppressed: list[str] | None = None,
    suppression_entries: list[str] | None = None,
    cleared_suppressions: list[str] | None = None,
    seeded_platforms: list[str] | None = None,
    platforms: tuple[str, ...] = ('cli',),
    toolsets: tuple[str, ...] = REQUIRED_SESSION_TOOLSETS,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'ok': False,
        'reason': reason,
        'detail': detail,
        'added': list(added or []),
        'already_present': list(already_present or []),
        'platforms': list(platforms),
        'toolsets': list(toolsets),
    }
    if focus_override is not None:
        payload['focus_override'] = focus_override
    if suppressed is not None:
        payload['suppressed_required_toolsets'] = list(suppressed)
    if suppression_entries is not None:
        payload['suppression_entries'] = list(suppression_entries)
    if cleared_suppressions is not None:
        payload['cleared_suppressions'] = list(cleared_suppressions)
    if seeded_platforms is not None:
        payload['seeded_platforms'] = list(seeded_platforms)
    return payload


def ensure_platform_toolsets(
    *,
    platforms: tuple[str, ...] = ('cli',),
    toolsets: tuple[str, ...] = REQUIRED_SESSION_TOOLSETS,
) -> dict[str, Any]:
    """Add required session toolsets to Hermes platform_toolsets without removing others.

    When a platform key is absent, seeds Hermes' platform-default composite first so
    materializing the list does not drop terminal/file and similar CLI capabilities.
    Malformed non-list values are rejected. After save, reloads and verifies persistence.
    Reports coding-focus overrides that bypass platform_toolsets for no-pin sessions.
    Lifts required names out of agent.disabled_toolsets (Hermes enable flow) or fails
    closed naming remaining suppressions.
    """
    try:
        from hermes_cli.config import load_config, save_config
    except Exception as exc:  # noqa: BLE001 -- config may be unavailable offline
        return _ensure_failure('config_unavailable', detail=type(exc).__name__, platforms=platforms, toolsets=toolsets)
    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001
        return _ensure_failure('config_unreadable', detail=type(exc).__name__, platforms=platforms, toolsets=toolsets)
    if not isinstance(config, dict):
        return _ensure_failure('config_invalid', detail='config_not_object', platforms=platforms, toolsets=toolsets)

    focus_override = _coding_focus_override(config)
    platform_toolsets = config.get('platform_toolsets')
    if platform_toolsets is None:
        platform_toolsets = {}
        config['platform_toolsets'] = platform_toolsets
    elif not isinstance(platform_toolsets, dict):
        return _ensure_failure(
            'config_invalid',
            detail='platform_toolsets_not_object',
            focus_override=focus_override,
            platforms=platforms,
            toolsets=toolsets,
        )

    added: list[str] = []
    already_present: list[str] = []
    seeded: list[str] = []
    intended_platform_toolsets: dict[str, list[str]] = {}
    changed = False
    for platform in platforms:
        if platform not in platform_toolsets:
            defaults = _platform_default_toolsets(config, platform)
            if defaults is None:
                return _ensure_failure(
                    'platform_default_unavailable',
                    detail=platform,
                    focus_override=focus_override,
                    platforms=platforms,
                    toolsets=toolsets,
                )
            current = list(defaults)
            platform_toolsets[platform] = current
            seeded.append(platform)
            changed = True
        else:
            current = platform_toolsets.get(platform)
        if not isinstance(current, list):
            return _ensure_failure(
                'config_invalid',
                detail=f'{platform}_toolsets_not_list',
                focus_override=focus_override,
                platforms=platforms,
                toolsets=toolsets,
            )
        normalized = [str(item) for item in current]
        platform_toolsets[platform] = normalized
        for toolset in toolsets:
            key = f'{platform}:{toolset}'
            if toolset in normalized:
                already_present.append(key)
            else:
                normalized.append(toolset)
                added.append(key)
                changed = True
        intended_platform_toolsets[platform] = list(normalized)

    suppression = _required_toolset_suppression(config, toolsets)
    if suppression is None:
        return _ensure_failure(
            'config_invalid',
            detail='disabled_toolsets_unreadable',
            added=added,
            already_present=already_present,
            focus_override=focus_override,
            seeded_platforms=seeded,
            platforms=platforms,
            toolsets=toolsets,
        )
    disabled = suppression['disabled']
    suppressed = suppression['suppressed']
    suppression_entries = suppression['suppressors']
    cleared_suppressions: list[str] = []
    direct_suppressed = [name for name in toolsets if name in set(disabled)]
    if direct_suppressed:
        agent = config.get('agent')
        if not isinstance(agent, dict):
            agent = {}
            config['agent'] = agent
        direct_set = set(direct_suppressed)
        remaining = [name for name in disabled if name not in direct_set]
        agent['disabled_toolsets'] = remaining
        cleared_suppressions = list(direct_suppressed)
        changed = True

    verify: dict[str, list[str]] | None = suppression
    if changed:
        try:
            save_config(config)
        except Exception as exc:  # noqa: BLE001
            return _ensure_failure(
                'config_unwritable',
                detail=type(exc).__name__,
                added=added,
                already_present=already_present,
                focus_override=focus_override,
                suppressed=suppressed or None,
                suppression_entries=suppression_entries or None,
                seeded_platforms=seeded,
                platforms=platforms,
                toolsets=toolsets,
            )
        try:
            reloaded = load_config()
        except Exception as exc:  # noqa: BLE001
            return _ensure_failure(
                'config_not_persisted',
                detail=type(exc).__name__,
                added=added,
                already_present=already_present,
                focus_override=focus_override,
                suppressed=suppressed or None,
                suppression_entries=suppression_entries or None,
                seeded_platforms=seeded,
                platforms=platforms,
                toolsets=toolsets,
            )
        if not isinstance(reloaded, dict):
            return _ensure_failure(
                'config_not_persisted',
                detail='reloaded_config_not_object',
                added=added,
                already_present=already_present,
                focus_override=focus_override,
                suppressed=suppressed or None,
                suppression_entries=suppression_entries or None,
                seeded_platforms=seeded,
                platforms=platforms,
                toolsets=toolsets,
            )
        reloaded_platforms = reloaded.get('platform_toolsets')
        if not isinstance(reloaded_platforms, dict):
            return _ensure_failure(
                'config_not_persisted',
                detail='platform_toolsets_missing_after_save',
                added=added,
                already_present=already_present,
                focus_override=focus_override,
                suppressed=suppressed or None,
                suppression_entries=suppression_entries or None,
                seeded_platforms=seeded,
                platforms=platforms,
                toolsets=toolsets,
            )
        for platform in platforms:
            persisted = reloaded_platforms.get(platform)
            if not isinstance(persisted, list):
                return _ensure_failure(
                    'config_not_persisted',
                    detail=f'{platform}_missing_after_save',
                    added=added,
                    already_present=already_present,
                    focus_override=focus_override,
                    suppressed=suppressed or None,
                    suppression_entries=suppression_entries or None,
                    seeded_platforms=seeded,
                    platforms=platforms,
                    toolsets=toolsets,
                )
            persisted_names = {str(item) for item in persisted}
            expected_names = intended_platform_toolsets.get(platform, [])
            missing = [name for name in expected_names if name not in persisted_names]
            if missing:
                return _ensure_failure(
                    'config_not_persisted',
                    detail=f'{platform}_entries_missing_after_save:' + ','.join(missing),
                    added=added,
                    already_present=already_present,
                    focus_override=focus_override,
                    suppressed=suppressed or None,
                    suppression_entries=suppression_entries or None,
                    seeded_platforms=seeded,
                    platforms=platforms,
                    toolsets=toolsets,
                )
        verify = _required_toolset_suppression(reloaded, toolsets)
        if verify is None:
            return _ensure_failure(
                'config_not_persisted',
                detail='disabled_toolsets_unreadable_after_save',
                added=added,
                already_present=already_present,
                focus_override=focus_override,
                suppressed=suppressed or None,
                suppression_entries=suppression_entries or None,
                seeded_platforms=seeded,
                platforms=platforms,
                toolsets=toolsets,
            )
    else:
        try:
            reloaded = load_config()
        except Exception as exc:  # noqa: BLE001
            return _ensure_failure(
                'config_unreadable',
                detail=type(exc).__name__,
                added=added,
                already_present=already_present,
                focus_override=focus_override,
                suppressed=suppressed or None,
                suppression_entries=suppression_entries or None,
                seeded_platforms=seeded,
                platforms=platforms,
                toolsets=toolsets,
            )
        if not isinstance(reloaded, dict):
            return _ensure_failure(
                'config_invalid',
                detail='config_not_object',
                added=added,
                already_present=already_present,
                focus_override=focus_override,
                suppressed=suppressed or None,
                suppression_entries=suppression_entries or None,
                seeded_platforms=seeded,
                platforms=platforms,
                toolsets=toolsets,
            )
        verify = _required_toolset_suppression(reloaded, toolsets)
        if verify is None:
            return _ensure_failure(
                'config_invalid',
                detail='disabled_toolsets_unreadable',
                added=added,
                already_present=already_present,
                focus_override=focus_override,
                suppressed=suppressed or None,
                suppression_entries=suppression_entries or None,
                seeded_platforms=seeded,
                platforms=platforms,
                toolsets=toolsets,
            )
    still = verify['suppressed']
    if still:
        entries = verify['suppressors']
        detail = (
            'disabled_by:' + ','.join(entries)
            if entries
            else 'suppressed:' + ','.join(still)
        )
        return _ensure_failure(
            'required_toolsets_suppressed',
            detail=detail,
            added=added,
            already_present=already_present,
            focus_override=focus_override,
            suppressed=still,
            suppression_entries=entries or None,
            cleared_suppressions=cleared_suppressions,
            seeded_platforms=seeded,
            platforms=platforms,
            toolsets=toolsets,
        )

    return {
        'ok': True,
        'reason': 'updated' if changed else 'unchanged',
        'detail': None,
        'added': added,
        'already_present': already_present,
        'seeded_platforms': seeded,
        'focus_override': focus_override,
        'cleared_suppressions': cleared_suppressions,
        'platforms': list(platforms),
        'toolsets': list(toolsets),
    }


def _tool_exposure_report(requested_toolsets: Any = None, *, seams: SimpleNamespace | None = None) -> dict[str, Any]:
    """Compare what this plugin registered with what a session would expose.

    "registered" means the Hermes registry holds this plugin's own handler under the tool's
    name. "callable" means the name is in the catalog Hermes builds for the selected toolsets.
    The two fail independently and each failure carries its own reason. A value that could not
    be determined is None; nothing is assumed available.
    """
    seams = _load_hermes_seams() if seams is None else seams
    report = _unavailable_exposure("hermes_registry_unavailable")
    tools = report["tools"]
    if seams.registry is None:
        return report
    try:
        entries = {name: seams.registry.get_entry(name) for name in TOOL_TOOLSETS}
    except Exception:  # noqa: BLE001 -- a registry API this plugin does not understand
        return _unavailable_exposure("hermes_registry_unreadable")
    report["evidence"] = "registry_only"
    report["unavailable_reason"] = None

    selection = None
    catalog = None
    try:
        selection = _resolve_selection(seams, requested_toolsets)
    except Exception:  # noqa: BLE001 -- the default selection could not be resolved
        report["unavailable_reason"] = "selection_unresolved"
    if selection is not None:
        report["selection"] = selection
        if seams.get_tool_definitions is None:
            report["unavailable_reason"] = "hermes_catalog_unavailable"
        else:
            try:
                catalog = _catalog_names(seams, selection["enabled_toolsets"], selection["disabled_toolsets"])
            except Exception:  # noqa: BLE001
                report["unavailable_reason"] = "catalog_query_failed"
    if catalog is not None:
        report["evidence"] = "hermes_tool_definitions"

    for name, tool in tools.items():
        entry = entries[name]
        own_handler = _REGISTERED_HANDLERS.get(name)
        if entry is None:
            tool.update(registered=False, reason="not_registered")
        else:
            tool["registry_toolset"] = getattr(entry, "toolset", None)
            if own_handler is None or getattr(entry, "handler", None) is not own_handler:
                tool.update(registered=False, reason="owned_by_another_registration")
            else:
                tool["registered"] = True
        if catalog is not None:
            tool["callable"] = name in catalog
            if tool["registered"] is True and tool["callable"] is False:
                tool["reason"] = _exposure_failure_reason(seams, entry, selection, name)
    return report


def _effective_provider() -> str | None:
    """Return the provider the configured route uses, or None when it cannot be resolved."""
    resolver = _ROUTE_STATUS.get("effective_provider")
    if resolver is None:
        return None
    try:
        provider = resolver()
    except Exception:  # noqa: BLE001 -- an invalid route is reported through the tools' own checks
        return None
    return provider if provider in PROVIDERS else None


def _configured_browser_executable() -> Any:
    """Return the operator's optional ``browser_executable`` setting, or None."""
    reader = _ROUTE_STATUS.get("browser_executable")
    if reader is None:
        return None
    try:
        return reader()
    except Exception:  # noqa: BLE001 -- an unreadable setting falls back to discovery
        return None


def _browser_status_lines(diagnostic: dict[str, Any]) -> list[str]:
    """Render the redacted startup diagnostic. It holds only closed-set codes and numbers."""
    lines = [
        f"Browser startup: {diagnostic.get('outcome')}"
        + (f" ({diagnostic['reason']})" if diagnostic.get("reason") else "")
        + (" after fallback" if diagnostic.get("fallback_used") else "")
    ]
    for index, attempt in enumerate(diagnostic.get("attempts") or [], start=1):
        exit_text = ""
        if attempt.get("exit_signal"):
            exit_text = f", signal {attempt['exit_signal']}"
        elif attempt.get("exit_code") is not None:
            exit_text = f", exit code {attempt['exit_code']}"
        timing = f", {attempt['startup_ms']} ms" if attempt.get("startup_ms") is not None else ""
        reasons = attempt.get("stderr_reasons") or []
        reason_text = f", stderr: {', '.join(reasons)}" if reasons else ""
        lines.append(
            f"  {index}. {attempt.get('browser_family')} ({attempt.get('executable_class')}, "
            f"{attempt.get('source')}): {attempt.get('outcome')}{exit_text}{timing}{reason_text}"
        )
    return lines


def _credential_ready(credential_presence: dict[str, bool], effective_provider: str | None) -> bool:
    """Return whether the key the configured route needs exists.

    A key for the other provider does not count: a route that resolves to one provider cannot
    use the other provider's key. When the route cannot be resolved, any key is accepted.
    """
    if effective_provider in credential_presence:
        return bool(credential_presence[effective_provider])
    return any(credential_presence.values())


def _overall_status(
    credential_presence: dict[str, bool], exposure: dict[str, Any], effective_provider: str | None = None
) -> str:
    """Collapse tool exposure and credentials into one readiness word, worst problem first."""
    states = list(exposure["tools"].values())
    if any(state["registered"] is False for state in states):
        return "tools_not_registered"
    if any(state["callable"] is False for state in states):
        return "tools_not_callable"
    if not _credential_ready(credential_presence, effective_provider):
        return "credential_required"
    if any(state["registered"] is None or state["callable"] is None for state in states):
        return "exposure_unverified"
    return "ready"


_EXPOSURE_ADVICE = {
    "not_registered": "Hermes holds no entry for it; enable the plugin and start a fresh session.",
    "owned_by_another_registration": (
        "the registry entry belongs to another registration (toolset {registry_toolset}); remove any "
        "duplicate or legacy copy of the plugin and start a fresh session."
    ),
    "toolset_not_selected": (
        "toolset {registry_toolset} is not selected; add it to --toolsets or enable it in `hermes tools`."
    ),
    "toolset_disabled": (
        "toolset {registry_toolset} is listed in agent.disabled_toolsets, which Hermes applies even to an "
        "explicit --toolsets pin; remove it from that list or enable it in `hermes tools`."
    ),
    "availability_check_failed": "its availability check returned false.",
    "not_in_catalog": "its toolset is selected and its check passes, but Hermes still left it out.",
}


def _exposure_lines(exposure: dict[str, Any]) -> list[str]:
    """Render the failing parts of an exposure report for a terminal."""
    lines = []
    selection = exposure["selection"]
    if selection is not None:
        names = ", ".join(selection["enabled_toolsets"]) or "(none)"
        lines.append(f"Session toolsets ({selection['source']}): {names}")
        if selection["disabled_toolsets"]:
            lines.append("Disabled by agent.disabled_toolsets: " + ", ".join(selection["disabled_toolsets"]))
        if selection["unknown_toolsets"]:
            lines.append("Unknown toolsets Hermes ignores: " + ", ".join(selection["unknown_toolsets"]))
    if exposure["evidence"] != "hermes_tool_definitions":
        lines.append(f"Callable catalog not verified: {exposure['unavailable_reason']}")
    for name, tool in exposure["tools"].items():
        advice = _EXPOSURE_ADVICE.get(tool["reason"], "").format(**tool)
        if tool["registered"] is False:
            lines.append(f"  {name}: NOT registered by this plugin ({tool['reason']}); {advice}")
        elif tool["callable"] is False:
            lines.append(f"  {name}: registered but NOT in the session's callable catalog ({tool['reason']}); {advice}")
    return lines


def _cli_handler(args):
    command = getattr(args, "switchyard_command", None) or getattr(args, "jev_command", None)
    if command == "scan-catalog":
        from .catalog_scan import scan_catalog
        report = scan_catalog(args.path)
        print(json.dumps(report, sort_keys=True))
        return 0 if report["coverage_complete"] and not report["findings"] else 1
    if command == "lint-skills":
        from .skill_lint import CatalogError, SCHEMA, discover_report, exit_code, format_report

        try:
            report = discover_report(include_style=getattr(args, "style", False))
        except Exception as exc:  # noqa: BLE001 -- registry/provider text must not reach output
            reason = "catalog_unavailable"
            if isinstance(exc, CatalogError) and str(exc) in {
                "invalid_catalog", "catalog_too_large", "catalog_too_confusable",
            }:
                reason = str(exc)
            failure = {"schema": SCHEMA, "status": "unavailable", "reason": reason}
            if getattr(args, "json_output", False):
                print(json.dumps(failure, sort_keys=True))
            else:
                print(f"Skill catalog unavailable ({reason}).")
            return 1
        if getattr(args, "json_output", False):
            print(json.dumps(report, sort_keys=True))
        else:
            limit = None if getattr(args, "all_findings", False) else getattr(args, "limit", 20)
            print(format_report(report, limit=limit))
        return exit_code(report, getattr(args, "fail_on", None))
    if command == "status":
        credential_presence = {}
        for provider in ("typesafe", "openrouter"):
            try:
                credential_presence[provider] = bool(_secret(provider))
            except Exception:  # local secret-store status can be unavailable or locked
                credential_presence[provider] = False
        try:
            exposure = _tool_exposure_report(getattr(args, "toolsets", None))
        except Exception:  # noqa: BLE001 -- a fault in the report must not hide the rest of status
            exposure = _unavailable_exposure("exposure_report_failed")
        effective_provider = _effective_provider()
        credential_missing = not _credential_ready(credential_presence, effective_provider)
        status = _overall_status(credential_presence, exposure, effective_provider)
        payload = {
            "plugin": "hermes-switchyard",
            "plugin_version": _plugin_version(),
            "status": status,
            "network": False,
            "credential_presence": credential_presence,
            "effective_provider": effective_provider,
            "plugin_loaded": _RUNTIME_STATUS["plugin_loaded"],
            "routing_mode": _RUNTIME_STATUS["routing_mode"],
            "consumer_mode": _RUNTIME_STATUS["consumer_mode"],
            "public_or_sanitized_data_ack": _RUNTIME_STATUS["public_or_sanitized_data_ack"],
            "automatic_skill_jev_mode": _RUNTIME_STATUS["automatic_skill_jev_mode"],
            "hosted_construction_allowed": _RUNTIME_STATUS["hosted_construction_allowed"],
            "model_route_adapter": _RUNTIME_STATUS.get("model_route_adapter"),
            "reasoning_effort_adapter": _RUNTIME_STATUS.get("reasoning_effort_adapter"),
            "defer_tool_schemas": _RUNTIME_STATUS.get("defer_tool_schemas"),
            "tool_exposure": exposure,
            "toolset_composition": _toolset_composition(),
        }
        try:
            legacy_warnings = legacy_cleanup.status_warnings()
        except Exception:  # noqa: BLE001 -- keep the rest of local status available
            legacy_warnings = ["Legacy artifact scan unavailable (local check failed)."]
        payload["legacy_warnings"] = legacy_warnings
        browser_diagnostic = None
        if getattr(args, "browser", False) is True:
            # Opt-in: launches one headless browser on about:blank, checks its
            # DevTools endpoint, and closes it. No page load and no network.
            try:
                browser_diagnostic = browser_use.probe_browser_startup(
                    browser_executable=_configured_browser_executable()
                )
            except Exception:  # noqa: BLE001 -- a probe fault must not hide the rest of status
                browser_diagnostic = browser_use.startup_diagnostic([], reason="browser_probe_failed")
            payload["browser_startup"] = browser_diagnostic
        exit_code = 0
        if getattr(args, "check", False):
            exit_code = int(
                status != "ready"
                or (
                    getattr(args, "browser", False) is True
                    and (browser_diagnostic or {}).get("outcome") != "started"
                )
            )
        if getattr(args, "json_output", False):
            print(json.dumps(payload, sort_keys=True))
            return exit_code
        setup_hint = "Run: hermes switchyard setup --provider typesafe"
        if effective_provider is not None and credential_missing and any(credential_presence.values()):
            setup_hint = (
                f"The configured route uses {effective_provider}, whose key is missing. "
                f"Run: hermes switchyard setup --provider {effective_provider}, "
                "or point jev_provider at the provider whose key exists."
            )
        if status == "credential_required":
            print(f"Hermes Switchyard: credential_required (local status only). {setup_hint}")
        else:
            print(f"Hermes Switchyard: {status} (local status only)")
        for line in _exposure_lines(exposure):
            print(line)
        composition = _toolset_composition()
        print(
            "Toolset composition: full surface needs "
            + " + ".join(composition["required_for_full_surface"])
            + "; jev_computer_use alone needs computer_use."
        )
        print(composition["plugin_doctor"])
        if status != "credential_required" and credential_missing:
            print(f"Credential: credential_required. {setup_hint}")
        if browser_diagnostic is not None:
            for line in _browser_status_lines(browser_diagnostic):
                print(line)
        for line in legacy_warnings:
            print(line)
        return exit_code
    if command == "cleanup":
        try:
            result = legacy_cleanup.cleanup_legacy_artifacts(apply=getattr(args, "apply", False) is True)
        except Exception:  # noqa: BLE001 -- never expose raw config or filesystem errors
            print("Legacy cleanup failed before a report could be prepared.")
            return 1
        for line in legacy_cleanup.format_cleanup_report(result):
            print(line)
        return 0 if result["status"] in {"clean", "planned", "applied"} else 1
    if command == "guide":
        print(_after_install_text())
        return 0
    if command == "test":
        if getattr(args, "live", False) is not True or getattr(args, "public_or_sanitized_data_ack", False) is not True:
            print("Refusing live test: pass --live and --public-or-sanitized-data-ack.")
            return 2
        requested_provider = getattr(args, "provider", "auto")
        provider = requested_provider
        if provider == "auto":
            provider = "typesafe" if _secret("typesafe") else "openrouter"
        key = _secret(provider)
        if not key:
            print(json.dumps({"status": "failed", "reason": "credential_required"}, sort_keys=True))
            return 1
        endpoint = TYPESAFE_ENDPOINT if provider == "typesafe" else DEFAULT_ENDPOINT
        model = "jev-latest" if provider == "typesafe" else "typesafe/jev-1.13"
        active_client = DecisionClient(api_key=key, endpoint=endpoint, model=model)
        try:
            result = active_client.decide(
                {
                    "task": "Hermes Switchyard explicitly billed connectivity test",
                    "data_class": "public_synthetic",
                },
                {
                    "connectivity": {
                        "type": "noul",
                        "instructions": "Is this a bounded public synthetic connectivity test?",
                    }
                },
                public_or_sanitized_data_ack=True,
            )
        except Exception:  # provider and transport text must not reach CLI output
            print(json.dumps({"status": "failed", "reason": "provider_request_failed"}, sort_keys=True))
            return 1
        finally:
            active_client.close()
        print(json.dumps({
            "status": "passed",
            "provider": provider,
            "model": result.get("model"),
            "request_id": result.get("request_id"),
            "latency_ms": result.get("latency_ms"),
            "usage": result.get("usage") or {},
        }, sort_keys=True))
        return 0
    if command == "receipt":
        indent = None if getattr(args, "json_output", False) else 2
        human_output = getattr(args, "human_output", False)
        session = getattr(args, "session", None)
        last = getattr(args, "last", None)
        if session is None and last is None:
            receipt = receipt_state.read_latest_receipt()
            if receipt is None:
                if human_output:
                    print("No automatic skill-routing receipt is available.")
                else:
                    print(json.dumps({"status": "unavailable", "reason": "no_receipt"}, sort_keys=True))
                return 1
            if human_output:
                from .receipt_output import format_routing_receipt

                print(format_routing_receipt(receipt))
            else:
                print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=indent))
            return 0
        # History mode: per-session lookup and/or the newest N turn records.
        if last is not None and (type(last) is not int or last <= 0):
            print("--last requires a positive integer.")
            return 2
        if session is not None and receipt_history.sanitize_session_id(session) is None:
            print("--session requires a bounded ASCII session identifier.")
            return 2
        records = receipt_history.read_history(session_id=session, last=last)
        if not records:
            reason = "no_matching_receipts" if session is not None else "no_receipt_history"
            if human_output:
                message = "No routing receipts match these filters." if session is not None else "No routing receipt history is available."
                print(message)
            else:
                print(json.dumps({"status": "unavailable", "reason": reason}, sort_keys=True))
            return 1
        if human_output:
            from .receipt_output import format_routing_history

            print(format_routing_history(records))
        else:
            print(json.dumps(records, ensure_ascii=False, sort_keys=True, indent=indent))
        return 0
    if command == "stats":
        since = None
        since_value = getattr(args, "since", None)
        if since_value is not None:
            since = receipt_history.parse_since(since_value)
            if since is None:
                print("--since requires a window such as 30m, 24h, 7d, or 2w.")
                return 2
        stats = receipt_history.routing_stats(since=since)
        stats["reasoning_effort"] = effort_stats(since=since)
        indent = None if getattr(args, "json_output", False) else 2
        print(json.dumps(stats, ensure_ascii=False, sort_keys=True, indent=indent, allow_nan=False))
        return 0
    if command == "skill-duplicates":
        from .skill_duplicates import build_report, format_text

        if not 1 <= args.days <= 3650:
            print("--days requires an integer between 1 and 3650.")
            return 2
        report = build_report(days=args.days)
        print(json.dumps(report, sort_keys=True, allow_nan=False)
              if getattr(args, "json_output", False) else format_text(report))
        return 1 if report["status"] == "unavailable" else 0
    if command == "wow":
        from .wow import build_report, format_text

        days = args.days
        if days <= 0 or days > 3650:
            print("--days requires an integer between 1 and 3650.")
            return 2
        report = build_report(days=days, plugin_registered=_RUNTIME_STATUS["plugin_loaded"])
        print(json.dumps(report, sort_keys=True, allow_nan=False) if args.json_output else format_text(report))
        return 0
    if command == "ensure-toolsets":
        result = ensure_platform_toolsets()
        if getattr(args, "json_output", False):
            print(json.dumps(result, sort_keys=True))
            return 0 if result.get("ok") else 1
        elif result.get("ok"):
            if result.get("added"):
                print(
                    "Ensured session toolsets "
                    + ", ".join(result["toolsets"])
                    + f" on platforms {', '.join(result['platforms'])}. "
                    "Added: " + ", ".join(result["added"]) + ". Start a fresh session."
                )
            else:
                print(
                    "Required session toolsets already present: "
                    + ", ".join(result["toolsets"])
                    + "."
                )
            if result.get("seeded_platforms"):
                print(
                    "Seeded platform-default composites for: "
                    + ", ".join(result["seeded_platforms"])
                    + " before adding required toolsets."
                )
            focus = result.get("focus_override") or {}
            if focus.get("active"):
                print(focus.get("note") or "Coding focus posture overrides platform_toolsets for no-pin sessions.")
        else:
            reason = result.get("reason")
            detail = result.get("detail")
            suppressed = result.get("suppressed_required_toolsets") or []
            suffix = f": {detail}" if detail else ""
            print(f"Could not ensure toolsets ({reason}{suffix}).")
            if suppressed:
                print(
                    "Required toolsets remain in agent.disabled_toolsets: "
                    + ", ".join(suppressed)
                    + ". Remove them in `hermes tools` or clear the suppression list."
                )
            else:
                print(
                    "Enable Computer Use and Hermes Switchyard in `hermes tools`, "
                    'or pin both: hermes -t "computer_use,hermes_switchyard" chat'
                )
            return 1
        return 0
    if command != "setup":

        print(
            "Usage: hermes switchyard <status|cleanup|guide|setup|ensure-toolsets|receipt|stats|wow|skill-duplicates|test> "
            "[--provider ...|--json]"
        )
        return 2
    provider = args.provider
    key_name = "TYPESAFE_API_KEY" if provider == "typesafe" else "OPENROUTER_API_KEY"
    from hermes_cli.config import save_env_value
    from hermes_cli.secret_prompt import masked_secret_prompt

    value = masked_secret_prompt(f"{key_name}: ").strip()
    if not value:
        print("No credential saved.")
        return 1
    save_env_value(key_name, value)
    ensure_result = ensure_platform_toolsets()
    print(f"Saved {key_name} to the active Hermes profile secret store.")
    print(
        "Automatic hosted skill routing is on by default (`hosted_sanitized` + `load`); "
        "change it with `plugins.entries.hermes-switchyard.settings.automatic_skill_routing_mode`."
    )
    if ensure_result.get("ok") and ensure_result.get("added"):
        print(
            "Also ensured toolsets "
            + ", ".join(ensure_result["toolsets"])
            + " for CLI sessions (added "
            + ", ".join(ensure_result["added"])
            + ")."
        )
    elif ensure_result.get("ok"):
        print("Required CLI toolsets computer_use and hermes_switchyard are already configured.")
    else:
        print(
            "Could not auto-ensure toolsets; enable Computer Use in `hermes tools` "
            'or pin: hermes -t "computer_use,hermes_switchyard" chat'
        )
    focus = ensure_result.get("focus_override") or {}
    if focus.get("active"):
        print(focus.get("note") or "Coding focus posture overrides platform_toolsets for no-pin sessions.")
    print("Start a fresh session.")
    return 0


def _setup_cli(parser):
    commands = parser.add_subparsers(dest="switchyard_command")
    scan = commands.add_parser("scan-catalog", help="Read-only package/MCP review with content hashes and coverage gaps")
    scan.add_argument("path", help="Local package directory to inspect without executing it")
    lint = commands.add_parser("lint-skills", help="Report local skill-description routability hints")
    from .skill_lint import positive_limit

    lint.add_argument("--json", action="store_true", dest="json_output", help="Emit versioned JSON")
    lint.add_argument("--style", action="store_true", help="Include optional wording and length suggestions")
    lint.add_argument("--limit", type=positive_limit, default=20, metavar="N", help="Maximum text findings (default: 20; JSON is complete)")
    lint.add_argument("--all", action="store_true", dest="all_findings", help="Show every text finding; overrides --limit")
    lint.add_argument("--fail-on", choices=("warning", "error"), help="Exit 2 for findings at this severity or higher; default is advisory")
    setup = commands.add_parser("setup", help="Save one Jev provider key through a masked prompt")
    setup.add_argument("--provider", required=True, choices=("typesafe", "openrouter"))
    receipt = commands.add_parser("receipt", help="Show the latest automatic-routing receipt")
    receipt_output = receipt.add_mutually_exclusive_group()
    receipt_output.add_argument("--json", action="store_true", dest="json_output", help="Emit compact JSON")
    receipt_output.add_argument(
        "--human",
        action="store_true",
        dest="human_output",
        help="Show a readable summary of the decision and skill-load result",
    )
    receipt.add_argument(
        "--session",
        default=None,
        metavar="ID",
        help="Show retained history records for one session id, optionally capped with --last N",
    )
    receipt.add_argument(
        "--last",
        type=int,
        default=None,
        metavar="N",
        help="Show the newest N retained history records instead of the latest receipt",
    )
    stats = commands.add_parser("stats", help="Summarize the retained routing-history statistics")
    stats.add_argument(
        "--since",
        default=None,
        metavar="WINDOW",
        help="Only count records newer than this window, e.g. 30m, 24h, 7d, or 2w",
    )
    stats.add_argument("--json", action="store_true", dest="json_output", help="Emit compact JSON")
    duplicates = commands.add_parser(
        "skill-duplicates", help="Read-only audit of repeated skill_view reads after automatic loads"
    )
    duplicates.add_argument("--days", type=int, default=7, metavar="N", help="Trailing days (1-3650)")
    duplicates.add_argument("--json", action="store_true", dest="json_output", help="Emit JSON counts only")
    wow = commands.add_parser("wow", help="Summarize retained local observations only")
    wow.add_argument("--days", type=int, default=7, metavar="N", help="Trailing whole days (default: 7)")
    wow.add_argument("--json", action="store_true", dest="json_output", help="Emit schema-versioned JSON")
    status = commands.add_parser("status", help="Show local readiness without network access")
    status.add_argument("--json", action="store_true", dest="json_output")
    status.add_argument(
        "--check", action="store_true",
        help="Exit 1 unless local status is ready and any requested browser probe started",
    )
    status.add_argument(
        "--toolsets",
        default=None,
        metavar="NAMES",
        help=(
            "Comma-separated toolsets to evaluate, as passed to `hermes chat --toolsets`; "
            "default is the selection Hermes' CLI uses when --toolsets is not given"
        ),
    )
    status.add_argument(
        "--browser",
        action="store_true",
        help=(
            "Also launch one headless browser on about:blank, check it, close it, "
            "and report the redacted startup diagnostic (no network access)"
        ),
    )
    cleanup = commands.add_parser("cleanup", help="Review or archive exact legacy jev-decision artifacts")
    cleanup.add_argument("--apply", action="store_true", help="Back up and archive the planned artifacts")
    ensure = commands.add_parser(
        "ensure-toolsets",
        help=(
            "Add computer_use and hermes_switchyard to platform_toolsets.cli "
            "without enabling unrelated toolsets"
        ),
    )
    ensure.add_argument("--json", action="store_true", dest="json_output")
    commands.add_parser("guide", help="Show local usage guidance")
    test = commands.add_parser("test", help="Run the explicitly billed live provider test")
    test.add_argument("--live", action="store_true", help="Confirm that this may make a billed request")
    test.add_argument("--public-or-sanitized-data-ack", action="store_true", dest="public_or_sanitized_data_ack")
    test.add_argument("--provider", choices=("auto", "typesafe", "openrouter"), default="auto")
    parser.set_defaults(func=_cli_handler)


def _secret(provider: str = "auto"):
    from agent.secret_scope import get_secret

    names = {
        "typesafe": ("TYPESAFE_API_KEY",),
        "openrouter": ("OPENROUTER_API_KEY",),
        "auto": ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY"),
    }
    if provider not in names:
        raise ValueError("jev_provider must be auto, typesafe, or openrouter")
    for name in names[provider]:
        value = str(get_secret(name) or "").strip()
        if value:
            return value
    return ""


def _partial_accounting(exc_partial: Any) -> dict[str, Any]:
    """Summarize successful hosted calls recorded before a later failure."""
    from .automatic import _partial_accounting_metadata

    metadata = _partial_accounting_metadata(exc_partial)
    if not metadata:
        return {}
    return metadata


def _copy_redacted_jev_metadata_from_partial(exc_partial: Any) -> dict[str, Any]:
    """Copy redacted partial-accounting metadata without provider text."""
    summary = _partial_accounting(exc_partial)
    if not summary:
        return {}
    # Only bounded local metadata survives: counts, numeric latencies, typed
    # usage numbers, and identifiers that pass the receipt-safe identifier
    # carrier never receives arbitrary provider strings.
    allowed: dict[str, Any] = {}
    for field in ("request_count", "total_latency_ms"):
        value = summary.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            allowed[field] = value
    usage = summary.get("total_usage")
    if isinstance(usage, dict):
        bounded_usage = receipt_state.safe_usage(usage)
        if bounded_usage:
            allowed["total_usage"] = bounded_usage
            cost_is_unknown = bounded_usage.get("cost", "absent") is None
            allowed["cost_known"] = not cost_is_unknown
    for field in ("model", "request_id"):
        value = summary.get(field)
        if isinstance(value, str):
            bounded = receipt_state.safe_identifier(value, max_length=128)
            if bounded is not None:
                allowed[field] = bounded
    return allowed


def _error(exc):
    # Preserve observability with stable local codes, never arbitrary provider,
    # executor, UI, or credential text.
    if isinstance(exc, PermissionError):
        code, reason = "ack_required", "public_or_sanitized_data_ack is false"
    elif isinstance(exc, StaleTargetError):
        code, reason = "stale_target", "the exposed target identity changed"
    elif isinstance(exc, ValueError):
        code, reason = "invalid_request", "request validation failed"
    elif isinstance(exc, TypeError):
        code, reason = "invalid_response", "structured response validation failed"
    elif isinstance(exc, browser_use.BrowserStartupError) and exc.code == "snap_profile_unavailable":
        code, reason = "snap_profile_unavailable", "the Snap browser profile directory is unavailable"
    elif isinstance(exc, RuntimeError):
        code, reason = "execution_failed", "the bounded operation was not accepted"
    else:
        code, reason = "plugin_error", "the plugin operation failed"
    error = {"code": code, "reason": reason}
    # Partial accounting evidence recorded before a later failure survives the
    # public boundary through the same redacted metadata contract the success
    # path uses; the known subtotal is marked incomplete.
    if isinstance(exc, PartialAccountingError):
        partial_metadata = _copy_redacted_jev_metadata_from_partial(exc.partial)
        if partial_metadata:
            error["partial_accounting"] = partial_metadata
            error["total_usage_incomplete"] = True
    return json.dumps({"status": "error", "error": error})


def _resolved_public_data_ack(args, standing=True):
    if standing is not True:
        return False
    if "public_or_sanitized_data_ack" in args:
        return args.get("public_or_sanitized_data_ack") is True
    return True


def _require_public_data_ack(args, standing=True):
    if _resolved_public_data_ack(args, standing) is not True:
        raise PermissionError(
            "public_or_sanitized_data_ack is false; this call was refused"
        )


def _load_skill_excerpt(name: str) -> str | None:
    """Bounded SKILL.md excerpt for opt-in stage-2 detail (hosted_detail=excerpt)."""
    return skill_excerpt(_load_skill_context(name))


def _load_skill_context(name: str, *, task_id: str | None = None) -> str:
    """Load one exact skill through Hermes' supported skill loader."""
    from tools.skills_tool import skill_view

    response = (
        skill_view(name=name, task_id=task_id)
        if task_id is not None
        else skill_view(name=name)
    )
    payload = json.loads(response) if isinstance(response, str) else response
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise RuntimeError("skill loader rejected recommendation")
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("skill loader returned no content")
    return content


def register(ctx):
    default_steps = int(ctx_get_config(ctx, "computer_max_steps", default=100))
    approved_registry = ctx_get_config(ctx, "approved_model_registry", default=[])
    approved_registry_version = ctx_get_config(ctx, "approved_model_registry_version", default="")
    approved_registry_valid_until = ctx_get_config(ctx, "approved_model_registry_valid_until", default="")
    if hasattr(ctx, "register_cli_command"):
        ctx.register_cli_command(
            name="switchyard",
            help="Configure Hermes Switchyard Jev access",
            setup_fn=_setup_cli,
            handler_fn=_cli_handler,
        )
    register_host_auxiliary_task(
        ctx,
        "hermes_switchyard_writer",
        display_name="Jev Text Writer",
        description="Compose field text for Jev computer use from public or sanitized state only.",
        defaults={"timeout": 30},
    )

    def _route() -> tuple[str, str, str]:
        missing = object()
        configured_provider = ctx_get_config(ctx, "jev_provider", default=missing)
        configured_endpoint_value = ctx_get_config(ctx, "api_endpoint", default=missing)
        provider_explicit = configured_provider is not missing
        endpoint_explicit = configured_endpoint_value is not missing
        provider = "auto" if not provider_explicit else configured_provider
        configured_endpoint = DEFAULT_ENDPOINT if not endpoint_explicit else configured_endpoint_value
        configured_model = ctx_get_config(ctx, "jev_model", default=None)
        if provider not in {"auto", "typesafe", "openrouter"}:
            raise ValueError("jev_provider must be auto, typesafe, or openrouter")
        if configured_endpoint not in {DEFAULT_ENDPOINT, TYPESAFE_ENDPOINT}:
            raise ValueError("api_endpoint must be one of the fixed Jev endpoints")
        if provider_explicit and endpoint_explicit and (
            (provider == "openrouter" and configured_endpoint == TYPESAFE_ENDPOINT)
            or (provider == "typesafe" and configured_endpoint == DEFAULT_ENDPOINT)
        ):
            raise ValueError("jev_provider and api_endpoint select different providers")
        if configured_endpoint != DEFAULT_ENDPOINT:
            provider = "typesafe"
        elif provider == "auto":
            try:
                provider = "typesafe" if _secret("typesafe") else "openrouter"
            except Exception:
                provider = "openrouter"
        endpoint = TYPESAFE_ENDPOINT if provider == "typesafe" else DEFAULT_ENDPOINT
        model = configured_model or ("jev-latest" if provider == "typesafe" else "typesafe/jev-1.13")
        return endpoint, model, provider

    def client():
        endpoint, model, provider = _route()
        return DecisionClient(api_key=_secret(provider), endpoint=endpoint, model=model)

    def with_client(operation):
        active_client = client()
        try:
            return operation(active_client)
        finally:
            active_client.close()

    def decision_tools_available():
        try:
            _route()
            return True
        except Exception:  # noqa: BLE001 -- invalid route config stays hidden
            return False

    def effective_provider():
        """Return the provider the configured route resolves to, or None when it is invalid."""
        try:
            return _route()[2]
        except Exception:  # noqa: BLE001 -- an invalid route stays hidden, as in the tool checks
            return None

    _ROUTE_STATUS["effective_provider"] = effective_provider
    _ROUTE_STATUS["browser_executable"] = lambda: ctx_get_config(ctx, "browser_executable", default=None)

    def computer_route_available():
        # Catalog visibility matches the native computer-use surface. Jev
        # credentials are required at call time, not to advertise the tool.
        return sys.platform in {"win32", "darwin", "linux"}

    def cache_identity():
        """Return live route scope with only a one-way credential generation."""
        endpoint, model, provider = _route()
        credential = _secret(provider)
        credential_sha256 = hashlib.sha256(
            f"hermes-switchyard:{provider}:".encode("utf-8")
            + credential.encode("utf-8")
        ).hexdigest()
        return {
            "provider": provider,
            "endpoint": endpoint,
            "model": model,
            "profile": os.environ.get("HERMES_PROFILE", "default"),
            "credential_sha256": credential_sha256,
        }

    def setting_bool(key, default):
        value = ctx_get_config(ctx, key, default=default)
        return value if type(value) is bool else default

    from .browser_plan import BrowserPlanCache
    browser_plan_cache = BrowserPlanCache()

    standing_ack = setting_bool("public_or_sanitized_data_ack", True)
    if setting_bool("smart_approval_provider", False) and standing_ack:
        from .approval_review import register_approval_provider
        register_approval_provider(lambda: setting_bool("smart_approval_provider", False) and setting_bool("public_or_sanitized_data_ack", True))
    if setting_bool("consequential_tool_gate", False) and callable(getattr(ctx, "register_hook", None)):
        from .approval_review import pre_tool_gate
        ctx.register_hook("pre_tool_call", pre_tool_gate)

    from .output_pruning import OutputCompactionGuard
    from .stuck_detection import StuckDetector
    if callable(getattr(ctx, "register_hook", None)):
        pruning = setting_bool("repeated_output_compaction", False)
        stuck = StuckDetector(client) if standing_ack and setting_bool("cross_tool_stuck_detection", False) else None
        output_guard = OutputCompactionGuard() if pruning else None
        if pruning:
            ctx.register_hook("pre_llm_call", output_guard.capture)
        if pruning or stuck:
            def transform_result(**kwargs):
                advice = stuck(**kwargs) if stuck is not None and setting_bool("cross_tool_stuck_detection", False) and setting_bool("public_or_sanitized_data_ack", True) else None
                return advice if advice is not None else output_guard.prune(**kwargs) if output_guard is not None else None
            ctx.register_hook("transform_tool_result", transform_result)

    session_search_choice_confidence = _config_float(
        ctx_get_config(ctx, "session_search_rerank_choice_confidence_threshold", default=0.8),
        default=0.8,
        minimum=0.0,
        maximum=1.0,
    )
    session_search_winning_probability = _config_float(
        ctx_get_config(ctx, "session_search_rerank_winning_probability_threshold", default=0.8),
        default=0.8,
        minimum=0.0,
        maximum=1.0,
    )
    session_search_max_card_chars = int(
        _config_float(
            ctx_get_config(ctx, "session_search_rerank_max_card_chars", default=360),
            default=360.0,
            minimum=64.0,
            maximum=720.0,
        )
    )

    configured_routing_mode = ctx_get_config(ctx, "automatic_skill_routing_mode", default=None)
    if configured_routing_mode is None:
        # Unset profiles inherit the install default (hosted_sanitized). The
        # legacy automatic_skill_jev boolean never authorizes egress by itself.
        configured_routing_mode = DEFAULT_ROUTING_MODE
    automatic_hook = build_pre_llm_call_hook(
        enabled=setting_bool("automatic_skill_recommendation", True),
        configured_candidates=ctx_get_config(ctx, "automatic_skill_candidates", default=[]),
        routing_mode=configured_routing_mode,
        hosted_mode=ctx_get_config(ctx, "automatic_skill_jev_mode", default="always"),
        public_or_sanitized_data_ack=setting_bool(
            "automatic_skill_public_or_sanitized_data_ack",
            DEFAULT_AUTOMATIC_PUBLIC_OR_SANITIZED_DATA_ACK,
        ),
        client_factory=client,
        cache_identity=cache_identity,
        local_threshold=_config_float(
            ctx_get_config(ctx, "automatic_skill_local_threshold", default=0.20),
            0.20,
            minimum=0.0,
            maximum=1.0,
        ),
        local_margin=_config_float(
            ctx_get_config(ctx, "automatic_skill_local_margin", default=0.05),
            0.05,
            minimum=0.0,
            maximum=1.0,
        ),
        cache_seconds=_config_float(
            ctx_get_config(ctx, "automatic_skill_cache_seconds", default=30.0),
            30.0,
            minimum=0.0,
            maximum=300.0,
        ),
        deadline_seconds=_config_float(
            ctx_get_config(
                ctx,
                "automatic_skill_deadline_seconds",
                default=DEFAULT_AUTOMATIC_ROUTING_DEADLINE_SECONDS,
            ),
            DEFAULT_AUTOMATIC_ROUTING_DEADLINE_SECONDS,
            minimum=0.5,
            maximum=DEFAULT_OPERATION_DEADLINE_SECONDS,
        ),
        consumer_mode=ctx_get_config(
            ctx, "automatic_skill_consumer_mode", default=DEFAULT_CONSUMER_MODE
        ),
        skill_loader=_load_skill_context,
        mandatory_skills=discover_mandatory_skills(
            ctx_get_config(ctx, "automatic_skill_mandatory_skills", default=[])
        ),
        honor_no_skill_gate=setting_bool("automatic_skill_honor_no_skill_gate", False),
        light_turn_bypass=setting_bool("automatic_skill_light_turn_bypass", True),
        early_light_bypass_before_discover=setting_bool(
            "automatic_skill_early_light_bypass_before_discover", False
        ),
        two_stage=TwoStageConfig.from_mapping(
            {
                key: ctx_get_config(ctx, key, default=None)
                for key in TWO_STAGE_CONFIG_KEYS
            }
        ),
        excerpt_loader=_load_skill_excerpt,
    )
    _publish_runtime_status(
        routing_mode=configured_routing_mode,
        consumer_mode=ctx_get_config(ctx, "automatic_skill_consumer_mode", default=DEFAULT_CONSUMER_MODE),
        public_or_sanitized_data_ack=setting_bool(
            "automatic_skill_public_or_sanitized_data_ack",
            DEFAULT_AUTOMATIC_PUBLIC_OR_SANITIZED_DATA_ACK,
        ),
        automatic_skill_jev_mode=ctx_get_config(ctx, "automatic_skill_jev_mode", default="always"),
    )
    if automatic_hook is not None and hasattr(ctx, "register_hook"):
        ctx.register_hook("pre_llm_call", automatic_hook)


    # Hermes 0.19 has no model-selection apply seam. Register a safe no-op (or
    # bind a future seam) without changing the active model. Coordinators use
    # recommend_model_route / jev_model_route_approved for typed receipts.
    _RUNTIME_STATUS["model_route_adapter"] = register_model_route_adapter(ctx)

    # Hermes 0.21 exposes llm_request middleware + reasoning_effort. Adaptive
    # effort is the apply-able win; model route stays advisory (applied: false).
    defer_schemas_enabled = setting_bool("defer_switchyard_tool_schemas", False)
    # When both adaptive effort and schema deferral are on, register one composed
    # llm_request callback. Hermes keeps the last returned request from callbacks
    # that each see the original payload; a second shallow rewrite would drop the
    # effort change.
    _RUNTIME_STATUS["reasoning_effort_adapter"] = register_reasoning_effort_adapter(
        ctx,
        enabled=setting_bool("adaptive_reasoning_effort", True),
        default_effort=str(
            ctx_get_config(ctx, "adaptive_reasoning_effort_default", default="medium") or "medium"
        ),
        client_factory=client,
        public_or_sanitized_data_ack=standing_ack,
        # Bounded to 0.1-1.5 s by the adapter; 0.4 s by default (measured warm Jev p95).
        deadline_seconds=ctx_get_config(
            ctx,
            "adaptive_reasoning_effort_deadline_seconds",
            default=DEFAULT_ADAPTIVE_REASONING_DEADLINE_SECONDS,
        ),
        client_identity=cache_identity,
        mode=str(ctx_get_config(ctx, "adaptive_reasoning_effort_mode", default="auto") or "auto"),
        exclude_models=ctx_get_config(ctx, "adaptive_reasoning_effort_exclude_models", default=None),
        allow_raise=ctx_get_config(ctx, "adaptive_reasoning_effort_allow_raise", default=False),
        record_decision=append_effort_record,
        step_adaptation=ctx_get_config(ctx, "adaptive_reasoning_effort_step_adaptation", default=True),
        receipt_mode=ctx_get_config(ctx, "adaptive_reasoning_effort_receipt_mode", default=None),
        receipt_line=ctx_get_config(ctx, "adaptive_reasoning_effort_receipt_line", default=None),
        register_llm_request=(not defer_schemas_enabled),
    )

    from . import reasoning_effort_adapter as _effort_mod

    effort_cb = getattr(_effort_mod, "_ACTIVE_LLM_REQUEST_CALLBACK", None)
    defer_status = register_defer_tool_schemas_middleware(
        ctx,
        enabled=defer_schemas_enabled,
        chain_with=effort_cb if defer_schemas_enabled else None,
    )
    # Never leave callables in runtime status (JSON probe / status dumps).
    if isinstance(defer_status, dict):
        defer_status = {k: v for k, v in defer_status.items() if k != "callback"}
    _RUNTIME_STATUS["defer_tool_schemas"] = defer_status
    if defer_status.get("registered") and defer_status.get("composed_with_prior"):
        _RUNTIME_STATUS["reasoning_effort_adapter"] = (
            _effort_mod.mark_composed_llm_request_registered()
        )

    def assess_handler(args, **kwargs):
        try:
            _require_public_data_ack(args, standing=standing_ack)
            state = args.get("state")
            questions = args.get("questions")
            if not isinstance(questions, dict) or not questions:
                raise ValueError("questions must be a non-empty object")
            def assess(active_client):
                with request_budget_scope(
                    active_client,
                    MAX_DECISION_REQUESTS,
                    deadline_seconds=args.get("deadline_seconds", DEFAULT_OPERATION_DEADLINE_SECONDS),
                ):
                    return active_client.decide(
                        state,
                        questions,
                        public_or_sanitized_data_ack=_resolved_public_data_ack(args, standing_ack),
                    )
            return json.dumps(with_client(assess))
        except Exception as exc:  # noqa: BLE001 -- tool handlers return structured errors
            return _error(exc)

    def computer_handler(args, **kwargs):
        try:
            _require_public_data_ack(args, standing=standing_ack)
            ack = _resolved_public_data_ack(args, standing_ack)
            start_url = browser_use.requested_web_start(args.get("start_url"), args.get("goal") or "")
            if start_url:
                def run_dom(active_client):
                    return browser_use.run_browser_goal(
                        retrieved_screen_enabled=setting_bool("retrieved_screen_enabled", False),
                        plan_cache=browser_plan_cache if setting_bool("browser_plan_cache", False) else None,
                        cache_scope=cache_identity() if setting_bool("browser_plan_cache", False) else None,
                        goal=args.get("goal") or "",
                        start_url=start_url,
                        client=active_client,
                        max_steps=int(args.get("max_steps") or default_steps),
                        min_actions_before_done=int(args.get("min_actions_before_done") or 0),
                        public_or_sanitized_data_ack=ack,
                        deadline_seconds=args.get("deadline_seconds", DEFAULT_OPERATION_DEADLINE_SECONDS),
                        completion_condition=args.get("completion_condition"),
                        text_inputs=args.get("text_inputs"),
                        allowed_hotkeys=args.get("allowed_hotkeys"),
                        browser_executable=_configured_browser_executable(),
                    )
                return json.dumps(with_client(run_dom))
            def native_dispatch(tool_name, tool_args):
                return ctx.dispatch_tool(tool_name, tool_args, **kwargs)
            result = with_client(lambda active_client: run_computer_goal(
                goal=args.get("goal") or "",
                app=args.get("app") or "",
                max_steps=int(args.get("max_steps") or default_steps),
                min_actions_before_done=int(args.get("min_actions_before_done") or 0),
                dispatch=native_dispatch,
                client=active_client,
                text_inputs=args.get("text_inputs"),
                allowed_hotkeys=args.get("allowed_hotkeys"),
                public_or_sanitized_data_ack=ack,
                deadline_seconds=args.get("deadline_seconds", DEFAULT_OPERATION_DEADLINE_SECONDS),
            ))
            return json.dumps(result)
        except Exception as exc:  # noqa: BLE001 -- tool handlers return structured errors
            return _error(exc)

    def skill_handler(args, **kwargs):
        try:
            _require_public_data_ack(args, standing=standing_ack)
            return json.dumps(with_client(lambda active_client: select_skill(
                    task=str(args.get("task") or ""),
                    candidates=list(args.get("candidates") or []),
                    client=active_client,
                    choice_confidence_threshold=args.get("choice_confidence_threshold", schemas.DEFAULT_SKILL_CHOICE_CONFIDENCE_THRESHOLD),
                    needs_skill_threshold=args.get("needs_skill_threshold", schemas.DEFAULT_SKILL_NEEDS_THRESHOLD),
                    winning_probability_threshold=args.get("winning_probability_threshold", schemas.DEFAULT_SKILL_WINNING_PROBABILITY_THRESHOLD),
                    public_or_sanitized_data_ack=_resolved_public_data_ack(args, standing_ack),
                    deadline_seconds=args.get("deadline_seconds", DEFAULT_OPERATION_DEADLINE_SECONDS),
                )))
        except Exception as exc:  # noqa: BLE001 -- tool handlers return structured errors
            return _error(exc)

    def multi_skill_handler(args, **kwargs):
        try:
            _require_public_data_ack(args, standing=standing_ack)
            return json.dumps(with_client(lambda active_client: select_skills(
                    task=str(args.get("task") or ""),
                    candidates=list(args.get("candidates") or []),
                    client=active_client,
                    selection_threshold=args.get("selection_threshold", schemas.DEFAULT_SKILL_NEEDS_THRESHOLD),
                    max_selections=args.get("max_selections"),
                    public_or_sanitized_data_ack=_resolved_public_data_ack(args, standing_ack),
                    deadline_seconds=args.get("deadline_seconds", DEFAULT_OPERATION_DEADLINE_SECONDS),
                )))
        except Exception as exc:  # noqa: BLE001 -- tool handlers return structured errors
            return _error(exc)

    def route_handler(args, **kwargs):
        try:
            _require_public_data_ack(args, standing=standing_ack)
            return json.dumps(with_client(lambda active_client: route_model(
                    task=str(args.get("task") or ""),
                    candidates=list(args.get("candidates") or []),
                    requirements=dict(args.get("requirements") or {}),
                    client=active_client,
                    capability_fit_threshold=args.get("capability_fit_threshold", schemas.DEFAULT_MODEL_CAPABILITY_FIT_THRESHOLD),
                    public_or_sanitized_data_ack=_resolved_public_data_ack(args, standing_ack),
                    deadline_seconds=args.get("deadline_seconds", DEFAULT_OPERATION_DEADLINE_SECONDS),
                )))
        except Exception as exc:  # noqa: BLE001 -- tool handlers return structured errors
            return _error(exc)

    def approved_route_handler(args, **kwargs):
        try:
            _require_public_data_ack(args, standing=standing_ack)
            result = recommend_approved_model(
                task=str(args.get("task") or ""),
                requirements=dict(args.get("requirements") or {}),
                registry=approved_registry,
                registry_version=approved_registry_version,
                valid_until=approved_registry_valid_until,
                client=client(),
                capability_fit_threshold=args.get(
                    "capability_fit_threshold", schemas.DEFAULT_MODEL_CAPABILITY_FIT_THRESHOLD
                ),
                public_or_sanitized_data_ack=_resolved_public_data_ack(args, standing_ack),
            )
            return json.dumps(result)
        except Exception as exc:  # noqa: BLE001 -- tool handlers return structured errors
            return _error(exc)

    def session_search_rerank_handler(args, **kwargs):
        try:
            _require_public_data_ack(args, standing=standing_ack)
            query = str(args.get("query") or "")
            candidates = list(args.get("candidates") or [])
            choice_confidence_threshold = args.get(
                "choice_confidence_threshold",
                session_search_choice_confidence,
            )
            winning_probability_threshold = args.get(
                "winning_probability_threshold",
                session_search_winning_probability,
            )
            max_card_chars = args.get("max_card_chars", session_search_max_card_chars)
            pick_match_message = args.get("pick_match_message", True)
            ack = _resolved_public_data_ack(args, standing_ack)
            deadline_seconds = args.get("deadline_seconds", DEFAULT_OPERATION_DEADLINE_SECONDS)
            try:
                active_client = client()
            except Exception:  # noqa: BLE001 -- missing key/route fails open to FTS
                return json.dumps(
                    rerank_session_search(
                        retrieved_screen_enabled=setting_bool("retrieved_screen_enabled", False),
                        candidates=candidates,
                        query=query,
                        client=None,
                        choice_confidence_threshold=choice_confidence_threshold,
                        winning_probability_threshold=winning_probability_threshold,
                        max_card_chars=int(max_card_chars),
                        pick_match_message=bool(pick_match_message),
                        public_or_sanitized_data_ack=ack,
                        deadline_seconds=deadline_seconds,
                    )
                )
            try:
                return json.dumps(
                    rerank_session_search(
                        retrieved_screen_enabled=setting_bool("retrieved_screen_enabled", False),
                        query=query,
                        candidates=candidates,
                        client=active_client,
                        choice_confidence_threshold=choice_confidence_threshold,
                        winning_probability_threshold=winning_probability_threshold,
                        max_card_chars=max_card_chars,
                        pick_match_message=pick_match_message,
                        public_or_sanitized_data_ack=ack,
                        deadline_seconds=deadline_seconds,
                    )
                )
            finally:
                active_client.close()
        except Exception as exc:  # noqa: BLE001 -- tool handlers return structured errors
            return _error(exc)



    def register_tool(name, schema, handler, check_fn):
        _REGISTERED_HANDLERS[name] = handler
        ctx.register_tool(
            name=name,
            toolset=TOOL_TOOLSETS[name],
            schema=schema,
            handler=handler,
            check_fn=check_fn,
            emoji="⚡",
        )

    # Source finding is an optional pre-turn optimization, never a callable tool.
    finder_root = ctx_get_config(ctx, "evidence_finder_root", default="")
    if (ctx_get_config(ctx, "evidence_finder_enabled", default=False) is True
            and ctx_get_config(ctx, "public_or_sanitized_data_ack", default=True) is True
            and isinstance(finder_root, str) and finder_root and Path(finder_root).is_absolute()
            and callable(getattr(ctx, "register_hook", None)) and decision_tools_available()):
        from .source_prefetch import build_hook as build_source_prefetch_hook
        ctx.register_hook("pre_llm_call", build_source_prefetch_hook(
            enabled=True, root=finder_root, standing_ack=True, client_factory=client,
        ))
    register_tool("jev_assess", schemas.ASSESS, assess_handler, decision_tools_available)
    register_tool("jev_computer_use", schemas.COMPUTER_USE, computer_handler, computer_route_available)
    register_tool("jev_skill_select", schemas.SKILL_SELECT, skill_handler, decision_tools_available)
    register_tool(
        "jev_skill_select_many", schemas.MULTI_SKILL_SELECT, multi_skill_handler, decision_tools_available
    )
    register_tool("jev_model_route", schemas.MODEL_ROUTE, route_handler, decision_tools_available)
    register_tool(
        "jev_model_route_approved", schemas.MODEL_ROUTE_APPROVED, approved_route_handler, decision_tools_available
    )
    register_tool(
        "jev_session_search_rerank",
        schemas.SESSION_SEARCH_RERANK,
        session_search_rerank_handler,
        decision_tools_available,
    )
    if hasattr(ctx, "register_skill"):
        import inspect

        skill_path = Path(__file__).parent / "skills" / "hermes-switchyard-operations" / "SKILL.md"
        description = ""
        try:
            skill_text = skill_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            skill_text = ""
        # The bundled frontmatter uses a top-level, single-line description.
        frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", skill_text, re.DOTALL)
        if frontmatter is not None:
            match = re.search(r"^description:[ \t]*(.*)$", frontmatter.group(1), re.MULTILINE)
            if match is not None:
                description = match.group(1).strip()
        try:
            parameters = inspect.signature(ctx.register_skill).parameters.values()
        except (TypeError, ValueError):
            parameters = ()
        supports_description = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            or (parameter.name == "description" and parameter.kind in {
                inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY,
            })
            for parameter in parameters
        )
        kwargs = {"description": description} if supports_description else {}
        ctx.register_skill("hermes-switchyard-operations", skill_path, **kwargs)
    if sys.platform in {"win32", "darwin", "linux"} and hasattr(ctx, "register_system_prompt_section"):
        ctx.register_system_prompt_section(
            "hermes-switchyard.computer-use",
            "Jev computer use is registered by default on Windows, macOS, and Linux whenever the "
            "computer_use toolset is enabled. Prefer jev_computer_use for multi-step GUI goals. "
            "If the goal or start_url is a public https page, the plugin runs a DOM browser loop: "
            "one Jev request per step, page clicks, no Hermes computer_use between actions. "
            "Desktop apps without a URL still use Cua Driver. Standing public_or_sanitized_data_ack "
            "is on after install; omit the field. Pass false to refuse one call. That acknowledgement "
            "is not blanket egress authorization and does not override mandatory skills, the user's "
            "native/computer-use preference, or other required controls. DONE returns "
            "completion_candidate/verified=false. Use low-level computer_use for a single explicit "
            "atomic action or recovery.",
            position="after_memory",
            max_chars=900,
        )
