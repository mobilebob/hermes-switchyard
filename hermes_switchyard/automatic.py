"""Automatic, bounded skill routing through Hermes ``pre_llm_call``.

The hook defaults to ``load`` consumer mode with ``hosted_sanitized`` routing.
It can load one accepted skill through Hermes' normal ``skill_view`` loader
without changing a toolset or rewriting the cached system prompt. Local
matching supplies a deterministic fallback and a measured prefilter for hosted
fan-out. Hosted Jev runs when ``hosted_sanitized`` is selected, the consumer can
adopt (``load`` mode), standing acknowledgement is true, and either a host
allow envelope is present or the local restricted-pattern scan is clean
(``egress_authority: standing_ack``). Advisory recommendations never authorize
hosted work: delivery alone is not adoption. Explicit deny, unknown, malformed,
or restricted host envelopes still fail closed; restricted local scans still
fail closed. When a host allow envelope is present, the automatic hosted
payload contains only the envelope's bounded ``allowed_payload`` and exact
candidate identifiers; under standing ack with no envelope, the bounded local
task text that passed the scan is used instead. Conversation history, candidate
descriptions, and full skill bodies stay local. Before hosted partition
fan-out, a confidence-bounded shortlist may reduce the candidate set, and
``uncertain_only`` may apply a cheap local no-skill gate; insufficient margin
fails closed to the full catalog. Receipts record whether
``local_no_skill_gate``, ``local_prefilter_shortlist``, or full recall ran.

Every automatic turn records delivery, adoption, and outcome separately.
Outcome stays ``unverified`` at the plugin boundary so delivery is never claimed
as improvement. Explicit user skill instructions are resolved before any local
or hosted selection and suppress routing with zero provider requests.
"""
from __future__ import annotations

import hashlib
import os
import json
import logging
import math
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import egress_redaction, receipt_history, receipt_state
from .client import (
    DEFAULT_AUTOMATIC_ROUTING_DEADLINE_SECONDS,
    DeadlineExceeded,
    HostCancelled,
    LateResultDiscarded,
    PartialAccountingError,
    _defer_hosted_warning,
    _warn_hosted_failure,
    _validate_deadline_seconds,
    hosted_error_detail as classify_hosted_error,
    host_cancel_scope,
)
from .egress import (
    DEFAULT_CONSUMER_MODE,
    DEFAULT_ROUTING_MODE,
    EGRESS_AUTHORITY_STANDING_ACK,
    ROUTING_MODES,
    TurnEgressEvaluation,
    evaluate_turn_egress_policy,
    is_routing_mode,
)
from .reasoning_effort_adapter import _effort_scan_reason
from .routing import select_skill
from .trivial_turn import (  # noqa: F401 -- the limits stay importable from automatic
    LIGHT_NO_SKILL_REASON,
    TRIVIAL_ACK_MAX_WORDS,
    TRIVIAL_ACK_WORDS,
    TRIVIAL_SYMBOL_MAX_CHARS,
    hosted_skill_bypass_reason,
    is_trivial_turn,
)
from .two_stage_routing import (
    TwoStageConfig,
    detect_kanban_worker,
    platform_decision,
    run_two_stage,
)

logger = logging.getLogger(__name__)

LOCAL_DIAGNOSTIC_TOP_K = 32
MAX_TASK_CHARS = 4_000
MAX_CANDIDATE_NAME_CHARS = 128
MAX_DESCRIPTION_CHARS = 1_000
DEFAULT_LOCAL_THRESHOLD = 0.20
DEFAULT_LOCAL_MARGIN = 0.05
DEFAULT_CACHE_SECONDS = 30.0
MAX_CACHE_SECONDS = 300.0
DEFAULT_CACHE_SIZE = 32
# Hosted prefilter (issue #20): fail closed to full catalog when margin is weak.
DEFAULT_PREFILTER_SHORTLIST_SIZE = 32
DEFAULT_PREFILTER_NO_SKILL_THRESHOLD = 0.05
DEFAULT_PREFILTER_MIN_SCORE = 0.15
DEFAULT_PREFILTER_CUTOFF_MARGIN = 0.03
_CATALOG_FEATURE_CACHE_SIZE = 16
SHORTLIST_POLICY_NO_SKILL_GATE = "local_no_skill_gate"
SHORTLIST_POLICY_PREFILTER = "local_prefilter_shortlist"
SHORTLIST_POLICY_FULL_FAN_OUT = "full_partition_fan_out"
# Re-export for callers; kept below the typical Hermes ~30s callback budget.
DEFAULT_AUTOMATIC_DEADLINE_SECONDS = DEFAULT_AUTOMATIC_ROUTING_DEADLINE_SECONDS

# Catalog token-feature cache keyed by catalog hash (names + descriptions).
_CATALOG_FEATURE_CACHE: OrderedDict[str, tuple[frozenset[str], ...]] = OrderedDict()
_CATALOG_FEATURE_LOCK = threading.Lock()

# In-process skill registry discovery cache. skills_list() is fail-open and can
# be expensive on large profiles; automatic routing may also call discovery twice
# in one turn (catalog + explicit-override pool). Invalidate when any observed
# registry input changes (home/project/external roots, disabled set, platform,
# cwd, config) or when the entry exceeds the max age. Behavior is identical to
# uncached discovery aside from avoiding repeat registry scans.
_DISCOVERY_CACHE_LOCK = threading.Lock()
_DISCOVERY_CACHE: tuple[str, float, tuple[dict[str, str], ...]] | None = None
_DISCOVERY_CACHE_MAX_AGE_SECONDS = 30.0

# Stable, privacy-safe terminal states for the routing-receipt surface. These
# names identify every automatic-routing outcome without carrying task text,
# candidate descriptions, conversation history, or credentials.
RECEIPT_TERMINAL_STATES = receipt_state.RECEIPT_TERMINAL_STATES
# Stable local error codes for hosted failures. Provider exception text, local
# paths, usernames, and hostnames are never placed in a receipt.
HOSTED_ERROR_CODES = receipt_state.HOSTED_ERROR_CODES
# Field values that must never survive into a receipt, even if they are present
# on an intermediate recommendation result.
_RECEIPT_FORBIDDEN_MARKERS = (
    "SYNTHETIC_TASK_MARKER",
    "SYNTHETIC_CANDIDATE_DESCRIPTION_MARKER",
    "SYNTHETIC_CREDENTIAL_MARKER",
    "PRIVATE_HISTORY_MARKER",
    "Jev transport failed",
    "Jev connection failed",
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:+.-]*")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_PROMPT_INJECTION_RE = re.compile(
    r"\b(?:ignore\s+(?:all\s+)?previous|system\s+prompt|developer\s+message|"
    r"jailbreak|exfiltrat(?:e|ion)|reveal\s+(?:the\s+)?(?:secret|token|prompt))\b",
    re.IGNORECASE,
)
# Catalog metadata scan (skill names and stage-2 descriptions/excerpts). The
# user task no longer uses these checks: it is redacted, not blocked. Local
# catalog text is not user input and stays under the stricter blocklist.
_PAYMENT_RE = re.compile(
    r"\b(?:credit\s+card|card\s+number|cvv|cvc|bank\s+account|routing\s+number)\b|"
    r"\b(?:\d[ -]?){13,19}\b",
    re.IGNORECASE,
)
_VERIFICATION_RE = re.compile(
    r"\b(?:one[- ]time|verification|authenticator|mfa|2fa|otp)\b.{0,32}\b\d{4,10}\b",
    re.IGNORECASE,
)
_CONTACT_RE = re.compile(
    r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|\b(?:\+?\d[\d(). -]{7,}\d)\b|\b(?:ssn|social\s+security)\b",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|bearer\s+[A-Za-z0-9._~+/=-]{16,})\b|"
    r"\b(?:api[_ -]?key|access[_ -]?token|secret)\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)
_RESTRICTED_WORD_RE = re.compile(
    r"\b(?:credential|password|passphrase|hipaa|phi|"
    r"confidential)\b",
    re.IGNORECASE,
)

# The whole user message is scanned and redacted before the hosted cut, so a
# cut never exposes part of an unmasked value. Longer messages stay local.
MAX_SCANNED_TASK_CHARS = 64_000
REDACTION_UNAVAILABLE_REASON = egress_redaction.REDACTION_UNAVAILABLE_REASON

# Light-turn bypass: greetings / thanks / acknowledgements, greeting-class
# instructions, pure read-only listings, and short no-action explanations never
# name a specialist skill, so the hosted call only adds latency. Closed-list and
# predicate detectors live in trivial_turn.py; adaptive effort shares the ack list
# and greeting-class path.
TRIVIAL_TURN_REASON = "trivial_turn"
_trivial_turn = is_trivial_turn
_hosted_skill_bypass_reason = hosted_skill_bypass_reason
_TRIVIAL_ACK_WORDS = TRIVIAL_ACK_WORDS

# These words do not identify a specialist skill. Keeping this list local makes
# the default path deterministic and avoids an auxiliary model call.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "for",
        "from", "how", "i", "in", "is", "it", "me", "my", "of", "on", "or",
        "that", "the", "this", "to", "use", "with", "you", "your",
    }
)


def _coerce_bounded_text(value: Any, max_chars: int) -> str:
    """Return bounded text from a message value without reading other roles."""
    if isinstance(value, str):
        return value.strip()[:max_chars]
    if isinstance(value, list):
        parts: list[str] = []
        remaining = max_chars
        for block in value:
            if remaining <= 0:
                break
            # Only explicitly typed text parts cross the boundary: no bare strings,
            # documents, tool results, or image captions.
            if (
                isinstance(block, Mapping)
                and block.get("type") in ("text", "input_text")
                and isinstance(block.get("text"), str)
            ):
                text = block["text"]
            else:
                continue
            if parts:
                remaining -= 1
                if remaining <= 0:
                    break
            bounded = text[:remaining]
            parts.append(bounded)
            remaining -= len(bounded)
        return "\n".join(parts).strip()
    return ""


def _coerce_text(value: Any) -> str:
    """Return bounded user-task text for the hosted decision boundary."""
    return _coerce_bounded_text(value, MAX_TASK_CHARS)


def _tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(value.casefold()):
        if raw not in _STOPWORDS:
            tokens.add(raw)
        for part in re.split(r"[-_:+./]+", raw):
            if part and part not in _STOPWORDS:
                tokens.add(part)
    return tokens


def _local_scan_reason(value: Any, text: str) -> str | None:
    """Return a reason when text must stay local, before any hosted client exists.

    Only shapes that redaction cannot make safe keep text local: control
    characters, prompt-injection wording, opaque structured payloads, and
    restricted document markings (the same marking rule the effort path uses).
    Secret values are masked by :func:`_redact_for_hosted`, not blocked here.
    Topic words (password, confidential) and contact identifiers do not block.
    """
    if isinstance(value, Mapping):
        return "local_scan_unknown_structured"
    if not isinstance(value, (str, list, tuple)):
        return "local_scan_unreadable"
    if _CONTROL_CHAR_RE.search(text):
        return "local_scan_control_character"
    if _PROMPT_INJECTION_RE.search(text):
        return "local_scan_prompt_injection"
    marking = _effort_scan_reason(text)
    if marking is not None:
        return marking
    stripped = text.strip()
    structured_candidates = [stripped]
    embedded = re.search(r":\s*(?=[{\[])", stripped)
    if embedded:
        structured_candidates.append(stripped[embedded.end():])
    for candidate in structured_candidates:
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, (dict, list)):
            return "local_scan_unknown_structured"
    return None


def _catalog_scan_reason(value: Any, text: str) -> str | None:
    """Stricter scan for local catalog metadata that could cross the boundary.

    Skill names and stage-2 descriptions are not user input and are not
    redacted, so the pre-v0.5.5 blocklist still applies to them.
    """
    reason = _local_scan_reason(value, text)
    if reason is not None:
        return reason
    if _PAYMENT_RE.search(text):
        return "local_scan_payment_data"
    if _VERIFICATION_RE.search(text):
        return "local_scan_verification_data"
    if _CONTACT_RE.search(text):
        return "local_scan_contact_identifier"
    if _SECRET_VALUE_RE.search(text):
        return "local_scan_secret_like_value"
    if _RESTRICTED_WORD_RE.search(text):
        return "local_scan_restricted_data"
    return None


def _redact_for_hosted(text: str, max_chars: int = MAX_TASK_CHARS) -> tuple[str | None, str | None]:
    """Return ``(redacted bounded text, None)`` or ``(None, reason)``.

    The whole text is redacted with the Hermes egress scrubber before the cut,
    so the cut cannot expose part of an unmasked value. Without a Hermes
    redactor no text is sent and hosted routing is skipped.
    """
    redacted, reason = egress_redaction.redact_for_jev(text)
    if redacted is None:
        return None, reason or REDACTION_UNAVAILABLE_REASON
    return redacted[:max_chars].strip(), None


def _validate_name(name: Any) -> str:
    if type(name) is not str or not name or name != name.strip():
        raise ValueError("automatic skill candidate names must be non-empty exact strings")
    if len(name) > MAX_CANDIDATE_NAME_CHARS or any(ord(char) < 32 for char in name):
        raise ValueError("automatic skill candidate names must be short and control-character-free")
    return name


def _hosted_payload_scan_reason(task: str, candidates: list[dict[str, str]]) -> str | None:
    """Scan the exact fields that would cross the hosted boundary."""
    reason = _local_scan_reason(task, task)
    if reason is not None:
        return reason
    for candidate in candidates:
        name = candidate["name"]
        reason = _catalog_scan_reason(name, name)
        if reason is not None:
            return reason
    return None


def _validate_candidates(raw: Any, *, limit: int | None) -> tuple[dict[str, str], ...]:
    if not isinstance(raw, (list, tuple)) or not raw or (limit is not None and len(raw) > limit):
        bound = f"1 to {limit}" if limit is not None else "at least 1"
        raise ValueError(f"automatic skill candidates must contain {bound} entries")
    result: list[dict[str, str]] = []
    names: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            name = _validate_name(item)
            description = name
        elif isinstance(item, Mapping):
            name = _validate_name(item.get("name"))
            description = item.get("description", name)
            if type(description) is not str:
                raise ValueError(f"automatic skill candidate {name!r} description must be a string")
            description = description[:MAX_DESCRIPTION_CHARS]
        else:
            raise ValueError("automatic skill candidates must be strings or objects")
        if name in names:
            raise ValueError("automatic skill candidate names must be unique")
        names.add(name)
        result.append({"name": name, "description": description})
    return tuple(result)


def _active_hermes_home() -> Path | None:
    """Resolve the active Hermes home the same way ``skills_list`` does.

    Prefer ``hermes_constants.get_hermes_home()`` so context-local / multiplexed
    profile overrides are honored. Fall back to ``HERMES_HOME`` only when the
    helper is unavailable. Never logs or serializes the path into receipts.
    """
    try:
        from hermes_constants import get_hermes_home
    except (ImportError, AttributeError):
        get_hermes_home = None  # type: ignore[assignment]
    if callable(get_hermes_home):
        try:
            resolved = get_hermes_home()
        except Exception:  # noqa: BLE001 -- fingerprint must stay fail-open
            resolved = None
        if resolved is not None:
            try:
                return Path(resolved).expanduser()
            except (TypeError, ValueError):
                pass
    configured = os.environ.get("HERMES_HOME")
    if isinstance(configured, str) and configured.strip():
        return Path(configured.strip()).expanduser()
    return None


def _skills_registry_roots() -> tuple[Path, ...]:
    """Return skill roots used only for discovery-cache invalidation.

    Paths are never logged or written into receipts. Missing roots are fine:
    the fingerprint records absence so a later create invalidates the cache.
    Roots are keyed off the *active* Hermes home (``get_hermes_home()``), not
    process env alone, so multiplexed profiles do not cross-cache catalogs.

    When Hermes skill_utils helpers are importable, also include trusted project
    dirs, configured external dirs, and create_dir — the same extra roots
    ``skills_list()`` consults beyond the profile home.
    """
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path | None) -> None:
        if path is None:
            return
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen:
            return
        seen.add(key)
        roots.append(path)

    base = _active_hermes_home()
    if base is not None:
        _add(base / "skills")
        # Profile-scoped skills (when HERMES_PROFILE is set) live beside the
        # shared tree; include both so an install under either root refreshes.
        profile = os.environ.get("HERMES_PROFILE")
        if isinstance(profile, str) and profile.strip():
            _add(base / "profiles" / profile.strip() / "skills")
    # Extra registry inputs Hermes skills_list() merges in (fail-open).
    try:
        from agent.skill_utils import (  # type: ignore[import-not-found]
            get_external_skills_dirs,
            get_project_skills_dirs,
            get_skill_create_dir,
        )

        for getter in (get_project_skills_dirs, get_external_skills_dirs):
            try:
                for entry in getter() or ():
                    if isinstance(entry, (str, os.PathLike)):
                        _add(Path(entry))
            except Exception:  # noqa: BLE001 -- fingerprint must stay cheap
                continue
        try:
            create_dir = get_skill_create_dir()
        except Exception:  # noqa: BLE001
            create_dir = None
        if create_dir is not None:
            _add(Path(create_dir))
    except Exception:  # noqa: BLE001 -- plugin stays usable without Hermes internals
        pass
    return tuple(roots)


def _discovery_policy_parts() -> list[str]:
    """Non-path registry inputs that change skills_list() without an mtime bump.

    Covers disabled skill names, platform eligibility context, and config.yaml
    identity so a config-only disable / platform switch invalidates the cache.
    Never embeds absolute paths into the returned strings (hashed later).
    """
    parts: list[str] = []
    platform = (
        os.environ.get("HERMES_PLATFORM")
        or os.environ.get("HERMES_SESSION_PLATFORM")
        or ""
    )
    if isinstance(platform, str) and platform.strip():
        parts.append(f"platform:{platform.strip()}")
    try:
        import sys as _sys

        parts.append(f"sys_platform:{getattr(_sys, 'platform', '')}")
    except Exception:  # noqa: BLE001
        parts.append("sys_platform:unknown")
    # cwd identity: project skills vary by working directory.
    try:
        cwd = Path.cwd().resolve()
        parts.append(f"cwd:{cwd}")
    except OSError:
        parts.append("cwd:unknown")
    try:
        from agent.skill_utils import get_disabled_skill_names  # type: ignore[import-not-found]

        disabled = sorted(str(name) for name in (get_disabled_skill_names() or set()) if name)
        parts.append("disabled:" + ",".join(disabled))
    except Exception:  # noqa: BLE001
        parts.append("disabled:unavailable")
    # Plugin skills are merged by skills_list outside Hermes' filesystem cache.
    # Registration/removal need not touch any skill root or config file.
    try:
        from hermes_cli.plugins import get_plugin_manager

        metadata = get_plugin_manager().list_plugin_skill_metadata()
        encoded = json.dumps(metadata, sort_keys=True, default=str).encode("utf-8")
        parts.append("plugins:" + hashlib.sha256(encoded).hexdigest())
    except Exception:  # noqa: BLE001 -- no plugin registry on standalone hosts
        parts.append("plugins:unavailable")
    # Config mtime so disabled / external_dirs edits without skills-dir churn refresh.
    try:
        from hermes_cli.config import get_config_path  # type: ignore[import-not-found]

        cfg_path = get_config_path()
        if cfg_path is not None:
            try:
                st = Path(cfg_path).stat()
                parts.append(f"config:{st.st_mtime_ns}:{st.st_ino}:{st.st_size}")
            except OSError:
                parts.append("config:missing")
    except Exception:  # noqa: BLE001
        # Fall back to HERMES_HOME/config.yaml when the helper is unavailable.
        base = _active_hermes_home()
        if base is not None:
            cfg = base / "config.yaml"
            try:
                st = cfg.stat()
                parts.append(f"config:{st.st_mtime_ns}:{st.st_ino}:{st.st_size}")
            except OSError:
                parts.append("config:missing")
        else:
            parts.append("config:unavailable")
    return parts


def _discovery_fingerprint() -> str:
    """Cheap fingerprint of all skills_list() registry inputs we can observe.

    Includes profile/project/external skill roots (path + mtime + children),
    disabled set, platform, cwd, and config identity — not only the active home
    skills tree — so two turns that differ only by project dir, disable list, or
    platform do not reuse each other's catalog.
    """
    parts: list[str] = []
    for root in _skills_registry_roots():
        try:
            st = root.stat()
            parts.append(f"{root.resolve()}:{st.st_mtime_ns}:{st.st_ino}")
            if not root.is_dir():
                continue
            try:
                children = sorted(root.iterdir(), key=lambda p: p.name)
            except OSError:
                continue
            # Cap directory walk so a huge skills tree cannot dominate the turn.
            for child in children[:512]:
                try:
                    cst = child.stat()
                    parts.append(f"{child.name}:{cst.st_mtime_ns}:{cst.st_ino}")
                except OSError:
                    parts.append(f"{child.name}:unreadable")
        except OSError:
            parts.append(f"{root}:missing")
    parts.extend(_discovery_policy_parts())
    if not parts:
        parts.append("no_hermes_home")
    return hashlib.sha256("\n".join(parts).encode("utf-8", "backslashreplace")).hexdigest()


def clear_skill_discovery_cache() -> None:
    """Drop the in-process skills_list discovery cache (tests / forced refresh)."""
    global _DISCOVERY_CACHE
    with _DISCOVERY_CACHE_LOCK:
        _DISCOVERY_CACHE = None


def discover_available_skill_candidates(
    *, force_refresh: bool = False
) -> tuple[dict[str, str], ...]:
    """Discover the active profile's skills through Hermes' public skills API.

    ``pre_llm_call`` receives the conversation messages before Hermes prepends
    the cached system prompt, so that payload is not a reliable skill catalog.
    ``tools.skills_tool.skills_list()`` is the supported profile-scoped registry
    surface and already filters disabled/platform-ineligible skills. Descriptions
    remain local ranking metadata and are bounded before use.

    Results are cached in-process and invalidated when any observed registry
    input changes (profile/project/external skill-root mtimes, disabled set,
    platform, cwd, config identity) or after ``_DISCOVERY_CACHE_MAX_AGE_SECONDS``.
    Pass ``force_refresh=True`` to bypass the cache. Failures are not cached.
    """
    global _DISCOVERY_CACHE
    fingerprint = _discovery_fingerprint()
    now = time.monotonic()
    with _DISCOVERY_CACHE_LOCK:
        cached = _DISCOVERY_CACHE
        if (
            not force_refresh
            and cached is not None
            and cached[0] == fingerprint
            and (now - cached[1]) <= _DISCOVERY_CACHE_MAX_AGE_SECONDS
        ):
            return tuple(dict(candidate) for candidate in cached[2])
    try:
        from tools.skills_tool import skills_list

        response = skills_list()
        payload = json.loads(response) if isinstance(response, str) else response
        rows = payload.get("skills") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            return ()
        candidates: list[dict[str, str]] = []
        for item in rows:
            if not isinstance(item, Mapping):
                continue
            try:
                candidate = _validate_candidates(
                    [{
                        "name": item.get("name"),
                        "description": item.get("description") or item.get("name") or "",
                    }],
                    limit=1,
                )[0]
            except ValueError:
                continue
            candidates.append(candidate)
        result = tuple(candidates)
    except Exception as exc:  # noqa: BLE001 -- catalog discovery is fail-open
        logger.debug("skill registry discovery failed: %s", type(exc).__name__)
        return ()
    with _DISCOVERY_CACHE_LOCK:
        _DISCOVERY_CACHE = (
            fingerprint, time.monotonic(), tuple(dict(candidate) for candidate in result)
        )
    return result


def _catalog_identity(candidates: tuple[dict[str, str], ...]) -> str:
    """Return a stable hash for catalog names and descriptions."""
    material = [(item["name"], item["description"]) for item in candidates]
    try:
        encoded = json.dumps(
            material,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        encoded = repr(material).encode("utf-8", "backslashreplace")
    return hashlib.sha256(encoded).hexdigest()


def _cached_candidate_tokens(candidates: tuple[dict[str, str], ...]) -> tuple[frozenset[str], ...]:
    """Cache tokenized catalog features by catalog hash to avoid repeat work."""
    key = _catalog_identity(candidates)
    with _CATALOG_FEATURE_LOCK:
        hit = _CATALOG_FEATURE_CACHE.get(key)
        if hit is not None:
            _CATALOG_FEATURE_CACHE.move_to_end(key)
            return hit
    features = tuple(
        frozenset(_tokens(f"{candidate['name']} {candidate['description']}"))
        for candidate in candidates
    )
    with _CATALOG_FEATURE_LOCK:
        _CATALOG_FEATURE_CACHE[key] = features
        _CATALOG_FEATURE_CACHE.move_to_end(key)
        while len(_CATALOG_FEATURE_CACHE) > _CATALOG_FEATURE_CACHE_SIZE:
            _CATALOG_FEATURE_CACHE.popitem(last=False)
    return features


def _rank_candidates(task: str, candidates: tuple[dict[str, str], ...]) -> list[tuple[float, int, dict[str, str]]]:
    task_tokens = _tokens(task)
    candidate_token_sets = _cached_candidate_tokens(candidates)
    ranked: list[tuple[float, int, dict[str, str]]] = []
    for position, (candidate, candidate_tokens) in enumerate(zip(candidates, candidate_token_sets)):
        overlap = task_tokens & candidate_tokens
        if not task_tokens or not candidate_tokens:
            score = 0.0
        else:
            task_coverage = len(overlap) / len(task_tokens)
            candidate_coverage = len(overlap) / len(candidate_tokens)
            score = round((task_coverage * 0.6) + (candidate_coverage * 0.4), 6)
        ranked.append((score, position, candidate))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked


def _local_decision(
    ranked: list[tuple[float, int, dict[str, str]]],
    *,
    threshold: float,
    margin: float,
) -> tuple[str | None, str, float]:
    if not ranked or ranked[0][0] < threshold:
        return None, "no_local_match", ranked[0][0] if ranked else 0.0
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < margin:
        return None, "ambiguous_local_match", ranked[0][0]
    return ranked[0][2]["name"], "local_match", ranked[0][0]


def plan_hosted_prefilter(
    ranked: list[tuple[float, int, dict[str, str]]],
    *,
    catalog_size: int,
    no_skill_threshold: float = DEFAULT_PREFILTER_NO_SKILL_THRESHOLD,
    shortlist_size: int = DEFAULT_PREFILTER_SHORTLIST_SIZE,
    min_score: float = DEFAULT_PREFILTER_MIN_SCORE,
    cutoff_margin: float = DEFAULT_PREFILTER_CUTOFF_MARGIN,
) -> tuple[str, tuple[dict[str, str], ...] | None]:
    """Plan a fail-closed hosted candidate subset.

    Returns ``(policy, subset)``:

    - ``local_no_skill_gate`` with an empty subset: skip hosted work.
    - ``local_prefilter_shortlist`` with a non-empty subset: host only the subset.
    - ``full_partition_fan_out`` with ``None``: host the complete catalog.

    The shortlist path requires a clear score cutoff so recall is preserved when
    local evidence is weak or ambiguous. Catalogs that already fit the shortlist
    bound pass through to full recall without rewriting policy.
    """
    if catalog_size < 0:
        raise ValueError("catalog_size must be non-negative")
    if shortlist_size < 1:
        raise ValueError("shortlist_size must be positive")
    no_skill_threshold = _config_float(
        no_skill_threshold, DEFAULT_PREFILTER_NO_SKILL_THRESHOLD, minimum=0.0, maximum=1.0
    )
    min_score = _config_float(min_score, DEFAULT_PREFILTER_MIN_SCORE, minimum=0.0, maximum=1.0)
    cutoff_margin = _config_float(
        cutoff_margin, DEFAULT_PREFILTER_CUTOFF_MARGIN, minimum=0.0, maximum=1.0
    )
    if not ranked:
        return SHORTLIST_POLICY_NO_SKILL_GATE, ()
    top_score = ranked[0][0]
    if top_score < no_skill_threshold:
        return SHORTLIST_POLICY_NO_SKILL_GATE, ()
    if catalog_size <= shortlist_size:
        # Already within the cheap bound; leave select_skill's policy untouched.
        return SHORTLIST_POLICY_FULL_FAN_OUT, None
    if top_score < min_score:
        return SHORTLIST_POLICY_FULL_FAN_OUT, None
    eligible = [item for item in ranked if item[0] >= min_score][:shortlist_size]
    if not eligible:
        return SHORTLIST_POLICY_FULL_FAN_OUT, None
    if len(ranked) > len(eligible):
        floor = eligible[-1][0]
        next_score = ranked[len(eligible)][0]
        if next_score > 0.0 and floor - next_score < cutoff_margin:
            return SHORTLIST_POLICY_FULL_FAN_OUT, None
    shortlist = tuple(item[2] for item in eligible)
    return SHORTLIST_POLICY_PREFILTER, shortlist


class AutomaticSkillRecommender:
    """Bounded recommender with explicit local/hosted routing and safe caching."""

    def __init__(
        self,
        *,
        configured_candidates: Any = None,
        routing_mode: str | None = None,
        hosted_enabled: bool | None = None,
        hosted_mode: str = "always",
        public_or_sanitized_data_ack: bool = True,
        client_factory: Callable[[], Any] | None = None,
        cache_identity: Callable[[], Any] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        local_threshold: float = DEFAULT_LOCAL_THRESHOLD,
        local_margin: float = DEFAULT_LOCAL_MARGIN,
        cache_seconds: float = DEFAULT_CACHE_SECONDS,
        cache_size: int = DEFAULT_CACHE_SIZE,
        deadline_seconds: float = DEFAULT_AUTOMATIC_DEADLINE_SECONDS,
        adoption_capable: bool | None = None,
        prefilter_shortlist_size: int = DEFAULT_PREFILTER_SHORTLIST_SIZE,
        prefilter_no_skill_threshold: float = DEFAULT_PREFILTER_NO_SKILL_THRESHOLD,
        prefilter_min_score: float = DEFAULT_PREFILTER_MIN_SCORE,
        prefilter_cutoff_margin: float = DEFAULT_PREFILTER_CUTOFF_MARGIN,
        honor_no_skill_gate: bool = False,
        light_turn_bypass: bool = True,
        two_stage: TwoStageConfig | None = None,
        excerpt_loader: Callable[[str], Any] | None = None,
    ) -> None:
        self.configured_candidates = (
            _validate_candidates(configured_candidates, limit=None)
            if configured_candidates else ()
        )
        # ``hosted_enabled`` is retained only for callers using the legacy
        # constructor API. The plugin registration path always supplies an
        # explicit mode; unset configuration falls back to DEFAULT_ROUTING_MODE.
        if routing_mode is None:
            if hosted_enabled is True:
                routing_mode = "hosted_sanitized"
            elif hosted_enabled is False:
                routing_mode = "local_only"
            else:
                routing_mode = DEFAULT_ROUTING_MODE
        if not is_routing_mode(routing_mode):
            raise ValueError(f"routing_mode must be one of {sorted(ROUTING_MODES)!r}")
        self.routing_mode = routing_mode
        self.hosted_enabled = routing_mode == "hosted_sanitized"
        if hosted_mode not in {"uncertain_only", "always"}:
            raise ValueError("hosted_mode must be 'uncertain_only' or 'always'")
        self.hosted_mode = hosted_mode
        # Standing acknowledgement defaults on after install. recommend() returns
        # ack_required and skips the client only when it is explicitly false.
        # When true and no host envelope is supplied, a clean local scan allows
        # hosted construction with egress_authority=standing_ack.
        self.public_or_sanitized_data_ack = public_or_sanitized_data_ack is True
        self.client_factory = client_factory
        self.cache_identity = cache_identity
        self.cancel_check = cancel_check
        self.local_threshold = local_threshold
        self.local_margin = local_margin
        self.cache_seconds = max(0.0, min(float(cache_seconds), MAX_CACHE_SECONDS))
        self.cache_size = max(1, min(int(cache_size), 128))
        self.deadline_seconds = _validate_deadline_seconds(deadline_seconds)
        # None leaves hosted policy unchanged for direct library callers.
        # The pre_llm_call hook sets False for advisory mode so hosted work
        # abstains when the consumer cannot adopt the recommendation.
        self.adoption_capable = None if adoption_capable is None else bool(adoption_capable)
        self.prefilter_shortlist_size = max(1, min(int(prefilter_shortlist_size), 255))
        self.prefilter_no_skill_threshold = max(0.0, min(float(prefilter_no_skill_threshold), 1.0))
        self.prefilter_min_score = max(0.0, min(float(prefilter_min_score), 1.0))
        self.prefilter_cutoff_margin = max(0.0, min(float(prefilter_cutoff_margin), 1.0))
        # When true, honor local_no_skill_gate even under hosted_mode=always.
        # Default false preserves always-mode full fan-out on near-zero overlap
        # (short opaque tasks such as ``fix ci``). Opt in to cut needless tax.
        self.honor_no_skill_gate = honor_no_skill_gate is True
        self.light_turn_bypass = light_turn_bypass is True
        self._cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = threading.RLock()
        self._client: Any | None = None
        self._client_identity: str | None = None
        # None keeps the legacy select_skill path for direct library callers.
        self.two_stage = two_stage
        self.excerpt_loader = excerpt_loader
        self._extra_clients: list[Any] = []
        self.last_receipt: dict[str, Any] | None = None

    def _route_identity_digest(self) -> str:
        """Hash the live route/profile identity without retaining raw values."""
        identity: Any = None
        if self.cache_identity is not None:
            try:
                identity = self.cache_identity()
            except Exception:  # noqa: BLE001 - identity failure must not block local routing
                identity = {"unavailable": True}
        try:
            encoded = json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError):
            encoded = repr(identity).encode("utf-8", "backslashreplace")
        return hashlib.sha256(encoded).hexdigest()

    def _pooled_client(self) -> Any:
        """Return the client owned by this recommender for the live route."""
        if self.client_factory is None:
            raise RuntimeError("hosted client is unavailable")
        identity = self._route_identity_digest()
        with self._lock:
            if self._client is not None and self._client_identity == identity:
                return self._client
            for stale in [self._client, *self._extra_clients]:
                close = getattr(stale, "close", None)
                if callable(close):
                    close()
            self._extra_clients = []
            self._client = self.client_factory()
            self._client_identity = identity
            return self._client

    def _extra_pooled_clients(self, count: int) -> list[Any]:
        """Return ``count`` extra clients for parallel stage-1 partitions.

        Each DecisionClient serializes on its own connection lock, so true
        parallelism needs one client per in-flight request. Extras follow the
        primary client's route identity and are closed with it.
        """
        primary = self._pooled_client()
        with self._lock:
            if self._client is not primary:
                return []
            while len(self._extra_clients) < count and self.client_factory is not None:
                self._extra_clients.append(self.client_factory())
            return list(self._extra_clients[:count])

    def close(self) -> None:
        """Close the explicitly owned pooled client."""
        with self._lock:
            clients = [self._client, *self._extra_clients]
            self._client = None
            self._client_identity = None
            self._extra_clients = []
            for client in clients:
                if client is None:
                    continue
                close = getattr(client, "close", None)
                if callable(close):
                    close()

    def __enter__(self) -> "AutomaticSkillRecommender":
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()

    def _cache_key(
        self,
        task_text: str,
        candidate_set: tuple[dict[str, str], ...],
        policy_key: Any = None,
    ) -> str:
        material = {
            "task": task_text,
            "candidates": [(item["name"], item["description"]) for item in candidate_set],
            "routing_mode": self.routing_mode,
            "hosted_mode": self.hosted_mode,
            "two_stage": (
                None
                if self.two_stage is None
                else [
                    self.two_stage.enabled,
                    self.two_stage.hosted_detail,
                    self.two_stage.recheck_top_k,
                    self.two_stage.early_stop,
                ]
            ),
            "policy": policy_key,
            "route_identity": self._route_identity_digest(),
        }
        try:
            encoded = json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError):
            encoded = repr(material).encode("utf-8", "backslashreplace")
        return hashlib.sha256(encoded).hexdigest()

    def _cached(self, key: str) -> dict[str, Any] | None:
        if self.cache_seconds <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expires, result = entry
            if expires <= now:
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            return {**result, "cache_hit": True}

    def _store(self, key: str, result: dict[str, Any]) -> None:
        if self.cache_seconds <= 0:
            return
        with self._lock:
            self._cache[key] = (time.monotonic() + self.cache_seconds, dict(result))
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)

    def _record_receipt(self, result: dict[str, Any]) -> None:
        receipt = build_routing_receipt(result)
        self.last_receipt = receipt
        # Persistence is best-effort. A failure is reported in memory only
        # and never changes the recommendation.
        result.pop(RECEIPT_PERSIST_FAILED_KEY, None)
        if not _persist_receipt(receipt):
            result[RECEIPT_PERSIST_FAILED_KEY] = True

    @staticmethod
    def _empty_result(
        *, routing_mode: str, reason: str, status: str = "abstained", routing_status: str = "hosted_skipped"
    ) -> dict[str, Any]:
        return {
            "status": status,
            "selected": None,
            "source": "none",
            "abstention_reason": reason,
            "routing_mode": routing_mode,
            "routing_status": routing_status,
            "routing_reason": reason,
            "hosted_attempted": False,
            "hosted_skipped": reason,
            "candidate_count": 0,
            "candidates_considered": [],
            "cache_hit": False,
        }

    def recommend(
        self,
        task: Any,
        *,
        candidates: Any = None,
        candidates_from_prompt: bool = False,
        turn_egress_policy: Any = None,
        egress_policy: Any = None,
    ) -> dict[str, Any]:
        del candidates_from_prompt  # retained for callback compatibility
        if self.routing_mode == "off":
            result = self._empty_result(
                routing_mode=self.routing_mode,
                reason="routing_mode_off",
                routing_status="disabled",
            )
            self._record_receipt(result)
            return result

        task_text = _coerce_text(task)
        if not task_text:
            result = self._empty_result(
                routing_mode=self.routing_mode,
                reason="empty_task",
                routing_status="local_abstention",
            )
            self._record_receipt(result)
            return result

        # Local-first light-turn bypass before catalog work or hosted calls.
        light_bypass = (
            _hosted_skill_bypass_reason(task_text) if self.light_turn_bypass else None
        )
        if light_bypass is not None:
            result = self._empty_result(
                routing_mode=self.routing_mode,
                reason=light_bypass,
                routing_status="hosted_skipped",
            )
            result["bypass_reason"] = light_bypass
            result["request_count"] = 0
            result["offered_count"] = 0
            result["input_chars"] = 0
            self._record_receipt(result)
            return result

        if self.configured_candidates:
            candidate_set = self.configured_candidates
        else:
            try:
                candidate_set = _validate_candidates(candidates, limit=None)
            except ValueError:
                candidate_set = ()
        if not candidate_set:
            result = self._empty_result(
                routing_mode=self.routing_mode,
                reason="no_candidates",
                routing_status="local_abstention",
            )
            self._record_receipt(result)
            return result

        evaluation: TurnEgressEvaluation | None = None
        if self.routing_mode == "hosted_sanitized":
            policy = turn_egress_policy if turn_egress_policy is not None else egress_policy
            if policy is not None:
                supplied = evaluate_turn_egress_policy(policy)
                if supplied.decision == "deny" or supplied.status == "unknown":
                    evaluation = supplied
                elif not self.public_or_sanitized_data_ack:
                    evaluation = TurnEgressEvaluation(
                        allowed=False,
                        decision="deny",
                        data_class=supplied.data_class,
                        status="denied",
                        reason_code="ack_required",
                        version=supplied.version,
                    )
                else:
                    evaluation = supplied
            elif not self.public_or_sanitized_data_ack:
                evaluation = TurnEgressEvaluation(
                    allowed=False,
                    decision="deny",
                    data_class=None,
                    status="denied",
                    reason_code="ack_required",
                    version=1,
                )
            else:
                # No host envelope: standing operator acknowledgement plus a
                # clean local scan authorize hosting. The scan keeps only
                # shapes that redaction cannot fix (markings, injection,
                # control characters, opaque payloads). Secret values are
                # masked below with the Hermes egress redactor before the
                # cut. Authorization is recorded as egress_authority=standing_ack.
                scan_text = _coerce_bounded_text(task, MAX_SCANNED_TASK_CHARS + 1)
                if len(scan_text) > MAX_SCANNED_TASK_CHARS:
                    scan_reason = "local_scan_oversized"
                else:
                    scan_reason = _local_scan_reason(task, scan_text)
                if scan_reason is None:
                    # Do not claim a positive data-class or policy version: the
                    # local scan is a blocklist, not a classifier. Authority is
                    # standing_ack alone.
                    evaluation = TurnEgressEvaluation(
                        allowed=True,
                        decision="allow",
                        data_class=None,
                        status="allowed",
                        reason_code="standing_ack_allowed",
                        allowed_payload=scan_text,
                        version=None,
                        egress_authority=EGRESS_AUTHORITY_STANDING_ACK,
                    )
                else:
                    evaluation = TurnEgressEvaluation(
                        allowed=False,
                        decision="deny",
                        data_class="unknown",
                        status="denied",
                        reason_code=scan_reason,
                        version=1,
                    )

        policy_key = evaluation.cache_key if evaluation is not None else None
        key = self._cache_key(task_text, candidate_set, policy_key)
        cached = self._cached(key)
        if cached is not None:
            cached["hosted_attempted"] = False
            self._record_receipt(cached)
            return cached

        ranked = _rank_candidates(task_text, candidate_set)
        local_selected, local_reason, local_score = _local_decision(
            ranked,
            threshold=self.local_threshold,
            margin=self.local_margin,
        )
        top_candidates = [item[2] for item in ranked[:LOCAL_DIAGNOSTIC_TOP_K]]
        result: dict[str, Any] = {
            "status": "selected" if local_selected else "abstained",
            "selected": local_selected,
            "source": "local" if local_selected else "none",
            "abstention_reason": None if local_selected else local_reason,
            "local_score": local_score,
            "candidate_count": len(candidate_set),
            "candidates_considered": [item["name"] for item in top_candidates],
            "routing_mode": self.routing_mode,
            "routing_status": "local_selection" if local_selected else "local_abstention",
            "routing_reason": local_reason if not local_selected else "local_match",
            "hosted_attempted": False,
            "cache_hit": False,
        }
        if evaluation is not None:
            result.update(evaluation.metadata)

        should_host = self.hosted_mode == "always" or local_selected is None
        hosted_candidates = [{"name": item["name"]} for item in candidate_set]
        prefilter_policy = SHORTLIST_POLICY_FULL_FAN_OUT
        if should_host and self.routing_mode == "hosted_sanitized":
            prefilter_policy, prefilter_subset = plan_hosted_prefilter(
                ranked,
                catalog_size=len(candidate_set),
                no_skill_threshold=self.prefilter_no_skill_threshold,
                shortlist_size=self.prefilter_shortlist_size,
                min_score=self.prefilter_min_score,
                cutoff_margin=self.prefilter_cutoff_margin,
            )
            # ``always`` historically forced full fan-out even on near-zero local
            # overlap (opaque identifiers). Default honor_no_skill_gate=False keeps
            # that always override; set true to skip needless zero-overlap tax.
            if (
                prefilter_policy == SHORTLIST_POLICY_NO_SKILL_GATE
                and self.hosted_mode == "always"
                and not self.honor_no_skill_gate
            ):
                prefilter_policy = SHORTLIST_POLICY_FULL_FAN_OUT
                prefilter_subset = None
            if prefilter_policy == SHORTLIST_POLICY_PREFILTER and prefilter_subset is not None:
                hosted_candidates = [{"name": item["name"]} for item in prefilter_subset]
        outbound_scan_reason = (
            _hosted_payload_scan_reason(
                evaluation.allowed_payload if evaluation is not None and evaluation.allowed_payload is not None else "",
                hosted_candidates,
            )
            if should_host and evaluation is not None and evaluation.allowed
            else None
        )
        if self.routing_mode == "local_only":
            result["hosted_skipped"] = "routing_mode_local_only"
            result["routing_status"] = "local_selection" if local_selected else "local_abstention"
            result["routing_reason"] = local_reason if not local_selected else "local_match"
        elif self.adoption_capable is False:
            # Advisory (or any non-adopting) consumer cannot turn a hosted
            # recommendation into an observable skill load. Abstain from hosted
            # work rather than paying cost/latency for delivery-only advice.
            result["hosted_skipped"] = "consumer_contract_unmet"
            result["routing_status"] = "hosted_skipped"
            result["routing_reason"] = "consumer_contract_unmet"
        elif evaluation is not None and not evaluation.allowed:
            # This branch occurs before client_factory() by design. Local
            # matching is still allowed because it does not cross the boundary.
            result["hosted_skipped"] = evaluation.reason_code
            result["routing_status"] = "hosted_skipped"
            result["routing_reason"] = evaluation.reason_code
        elif not should_host:
            result["hosted_skipped"] = "local_confident"
            result["bypass_reason"] = "local_confident"
            result["routing_status"] = "hosted_skipped"
            result["routing_reason"] = "local_confident"
        elif outbound_scan_reason is not None:
            result["hosted_skipped"] = outbound_scan_reason
            result["routing_status"] = "hosted_skipped"
            result["routing_reason"] = outbound_scan_reason
        elif prefilter_policy == SHORTLIST_POLICY_NO_SKILL_GATE:
            # Cheap local gate (uncertain_only only): near-zero lexical overlap
            # skips hosted fan-out after outbound identifier scanning.
            result["hosted_skipped"] = SHORTLIST_POLICY_NO_SKILL_GATE
            result["bypass_reason"] = SHORTLIST_POLICY_NO_SKILL_GATE
            result["shortlist_policy"] = SHORTLIST_POLICY_NO_SKILL_GATE
            result["routing_status"] = "hosted_skipped"
            result["routing_reason"] = SHORTLIST_POLICY_NO_SKILL_GATE
            result["offered_count"] = 0
            result["excluded_count"] = len(candidate_set)
            result["request_count"] = 0
            if local_selected is None:
                result["status"] = "abstained"
                result["selected"] = None
                result["source"] = "none"
                result["abstention_reason"] = SHORTLIST_POLICY_NO_SKILL_GATE
        elif self.client_factory is None:
            result["hosted_skipped"] = "client_unavailable"
            result["routing_status"] = "hosted_skipped"
            result["routing_reason"] = "client_unavailable"
        elif (hosted_task := _redact_for_hosted(
            evaluation.allowed_payload or "" if evaluation is not None else ""
        ))[0] is None:
            # No Hermes redactor: send no text. Local routing still applies.
            result["hosted_skipped"] = hosted_task[1]
            result["routing_status"] = "hosted_skipped"
            result["routing_reason"] = hosted_task[1]
        else:
            # Only the authorized bounded payload crosses this boundary (host
            # envelope allowed_payload, or standing-ack task text), after the
            # Hermes egress redactor masks secret values. History,
            # descriptions, and skill bodies do not.
            outbound_task = hosted_task[0] or ""
            result["hosted_attempted"] = True
            result["input_chars"] = len(outbound_task)
            # Intervention timeout is separate from the 60s explicit-tool /
            # computer-use deadline and from the per-request provider I/O timeout.
            result["intervention_deadline_seconds"] = self.deadline_seconds
            try:
                with host_cancel_scope(self.cancel_check), _defer_hosted_warning():
                    if self.two_stage is not None and self.two_stage.enabled:
                        # Stage 1 sends names only. Local descriptions reach
                        # stage 2 only when hosted_detail opts in, and only
                        # for the top-K finalists after a local scan.
                        by_name = {item["name"]: item for item in candidate_set}
                        hosted = run_two_stage(
                            task=outbound_task,
                            candidates=[by_name[item["name"]] for item in hosted_candidates],
                            client=self._pooled_client(),
                            client_pool=self._extra_pooled_clients(
                                self.two_stage.parallel_requests - 1
                            ),
                            config=self.two_stage,
                            excerpt_loader=self.excerpt_loader,
                            deadline_seconds=self.deadline_seconds,
                        )
                    else:
                        hosted = select_skill(
                            task=outbound_task,
                            candidates=hosted_candidates,
                            client=self._pooled_client(),
                            public_or_sanitized_data_ack=True,
                            deadline_seconds=self.deadline_seconds,
                        )
            except PartialAccountingError as exc:
                logger.debug("automatic Jev skill recommendation unavailable: %s", type(exc).__name__)
                result["hosted_error"] = _hosted_error_code(exc)
                result["hosted_error_code"] = result["hosted_error"]
                result["hosted_error_detail"] = classify_hosted_error(exc)
                _copy_redacted_jev_metadata(result, _partial_accounting_metadata(exc.partial))
                _warn_hosted_failure(result["hosted_error_detail"])
                hosted = None
            except Exception as exc:  # noqa: BLE001 -- automatic hook must fail open
                logger.debug("automatic Jev skill recommendation unavailable: %s", type(exc).__name__)
                result["hosted_error"] = _hosted_error_code(exc)
                result["hosted_error_code"] = result["hosted_error"]
                result["hosted_error_detail"] = classify_hosted_error(exc)
                _warn_hosted_failure(result["hosted_error_detail"])
                hosted = None
            if isinstance(hosted, dict):
                _copy_redacted_jev_metadata(result, hosted)
            if prefilter_policy == SHORTLIST_POLICY_PREFILTER:
                offered = len(hosted_candidates)
                excluded = len(candidate_set) - offered
                result["shortlist_policy"] = SHORTLIST_POLICY_PREFILTER
                result["jev_shortlist_policy"] = SHORTLIST_POLICY_PREFILTER
                result["offered_count"] = offered
                result["excluded_count"] = excluded
                result["jev_offered_count"] = offered
                result["jev_excluded_count"] = excluded
            offered_names = {item["name"] for item in hosted_candidates}
            if isinstance(hosted, dict) and hosted.get("selected") in offered_names:
                result.update(
                    {
                        "status": "selected",
                        "selected": hosted["selected"],
                        "source": "jev",
                        "abstention_reason": None,
                        "routing_status": "hosted_selection",
                        "routing_reason": "hosted_selection",
                    }
                )
            elif hosted is None:
                # Preserve the categorized failure code (deadline / cancel / late
                # discard / transport). A local winner may still be kept.
                error_code = result.get("hosted_error")
                if error_code not in HOSTED_ERROR_CODES:
                    error_code = "transport_or_execution_failure"
                result["hosted_error"] = error_code
                result["hosted_error_code"] = error_code
                result["routing_status"] = "hosted_failure_local_fallback" if local_selected else "hosted_failure"
                if error_code in {"deadline_exceeded", "host_cancelled", "late_result_discarded"}:
                    result["routing_reason"] = error_code
                else:
                    result["routing_reason"] = "hosted_request_failed"
                if local_selected:
                    result["status"] = "selected"
                    result["source"] = "local"
                    result["abstention_reason"] = None
            else:
                # Any structurally valid hosted response without an offered
                # selection is a deliberate hosted abstention.
                result.update(
                    {
                        "status": "abstained",
                        "selected": None,
                        "source": "none",
                        "abstention_reason": "hosted_abstention",
                        "routing_status": "hosted_abstention",
                        "routing_reason": "hosted_abstention",
                    }
                )

        if result["selected"] is not None:
            result["status"] = "selected"
        self._store(key, result)
        self._record_receipt(result)
        return result


def _copy_redacted_jev_metadata(result: dict[str, Any], hosted: Mapping[str, Any]) -> None:
    """Copy only bounded non-payload Jev metadata into an internal result."""
    for field in (
        "latency_ms", "total_latency_ms", "request_count", "offered_count", "excluded_count",
    ):
        value = hosted.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            result[f"jev_{field}"] = value
    for field in ("model", "request_id", "shortlist_policy"):
        value = hosted.get(field)
        if isinstance(value, str) and 0 < len(value) <= 128 and value.isprintable():
            result[f"jev_{field}"] = value
    for field in ("usage", "total_usage"):
        usage = hosted.get(field)
        if isinstance(usage, Mapping):
            bounded_usage = receipt_state.safe_usage(usage)
            if bounded_usage:
                result[f"jev_{field}"] = bounded_usage


def _partial_accounting_metadata(partial: Any) -> dict[str, Any]:
    """Summarize successful hosted calls recorded before a later failure."""
    if not isinstance(partial, list) or not partial:
        return {}
    total_latency_ms = 0.0
    total_usage: dict[str, float] = {}
    request_count = 0
    last: Mapping[str, Any] | None = None
    for item in partial:
        if not isinstance(item, Mapping):
            continue
        has_latency = (
            isinstance(item.get("latency_ms"), (int, float))
            and not isinstance(item.get("latency_ms"), bool)
            and math.isfinite(item.get("latency_ms"))
            and item.get("latency_ms") >= 0
        ) or (
            isinstance(item.get("total_latency_ms"), (int, float))
            and not isinstance(item.get("total_latency_ms"), bool)
            and math.isfinite(item.get("total_latency_ms"))
            and item.get("total_latency_ms") >= 0
        )
        has_identifier = any(isinstance(item.get(field), str) and item.get(field) for field in ("model", "request_id"))
        has_usage = isinstance(item.get("total_usage"), Mapping) or isinstance(item.get("usage"), Mapping)
        entry_request_count = item.get("request_count")
        if isinstance(entry_request_count, int) and not isinstance(entry_request_count, bool) and entry_request_count > 0:
            count = entry_request_count
        elif has_latency or has_identifier or has_usage:
            count = 1
        else:
            continue
        last = item
        request_count += count
        latency = item.get("total_latency_ms", item.get("latency_ms"))
        if isinstance(latency, (int, float)) and not isinstance(latency, bool) and math.isfinite(latency) and latency >= 0:
            total_latency_ms += float(latency)
        usage = item.get("total_usage")
        if not isinstance(usage, Mapping):
            usage = item.get("usage")
        if isinstance(usage, Mapping):
            for key, value in usage.items():
                if (
                    isinstance(key, str)
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                ):
                    total_usage[key] = total_usage.get(key, 0.0) + float(value)
    if request_count == 0 or last is None:
        return {}
    metadata: dict[str, Any] = {
        "request_count": request_count,
        "total_latency_ms": total_latency_ms,
    }
    if total_usage:
        metadata["total_usage"] = total_usage
    for field in ("latency_ms", "model", "request_id", "usage"):
        value = last.get(field)
        if value is not None:
            metadata[field] = value
    return metadata


def redacted_routing_metadata(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return status/reason metadata without task, payload, or provider text."""
    fields = (
        "routing_mode", "routing_status", "routing_reason", "status", "source",
        "selected", "hosted_attempted", "hosted_skipped", "hosted_error_code", "hosted_error_detail",
        "candidate_count", "bypass_reason", "input_chars", "cache_hit", "policy_status", "policy_reason",
        "policy_data_class", "policy_version", "egress_authority",
        "intervention_deadline_seconds", "receipt_persist_failed",
    )
    metadata: dict[str, Any] = {}
    for field in fields:
        value = result.get(field)
        if field == "hosted_error_detail" and (
            type(value) is not str or value not in receipt_state.HOSTED_ERROR_DETAILS
        ):
            continue
        if value is not None:
            metadata[field] = value
    return metadata


def _config_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    if type(value) not in (int, float) or value != value or value in (float("inf"), float("-inf")):
        return default
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return float(value)


def _config_bool(value: Any, default: bool) -> bool:
    return value if type(value) is bool else default


def _hosted_error_code(exc: Exception) -> str:
    # Map any hosted failure to a stable local code. Provider, transport, and
    # executor details are never surfaced in a receipt. Only the typed
    # aggregate signals map to deadline/cancel/late-discard; a plain
    # TimeoutError from a provider or custom transport remains a transport
    # failure so intervention timeout stays distinct from provider I/O timeout.
    if isinstance(exc, PartialAccountingError) and isinstance(exc.__cause__, BaseException):
        cause_code = _hosted_error_code(exc.__cause__)  # type: ignore[arg-type]
        if cause_code in {"deadline_exceeded", "host_cancelled", "late_result_discarded"}:
            return cause_code
    if isinstance(exc, HostCancelled):
        return "host_cancelled"
    if isinstance(exc, LateResultDiscarded):
        return "late_result_discarded"
    if isinstance(exc, DeadlineExceeded):
        return "deadline_exceeded"
    if isinstance(exc, PermissionError):
        return "ack_required"
    if isinstance(exc, ValueError):
        if "budget exceeded" in str(exc):
            return "request_budget_exhausted"
        return "validation_failure"
    if isinstance(exc, TypeError):
        return "typed_response_failure"
    if isinstance(exc, (RuntimeError, TimeoutError)):
        return "transport_or_execution_failure"
    return "plugin_error"


def _terminal_state(result: dict) -> str:
    """Map a recommendation result onto exactly one stable receipt terminal state.

    The mapping reads only local, stable result fields. It never inspects task
    text, candidate descriptions, or conversation history.
    """
    if result.get("cache_hit") is True:
        return "cache_hit"
    selected = result.get("selected")
    if (
        result.get("hosted_attempted") is True
        and result.get("source") == "local"
        and isinstance(selected, str)
        and selected
        and result.get("hosted_error")
    ):
        return "hosted_failure_local_fallback"
    if (
        result.get("hosted_attempted") is True
        and result.get("hosted_error")
        and not (isinstance(selected, str) and selected)
    ):
        return "hosted_failure"
    if result.get("source") == "local" and isinstance(selected, str) and selected:
        return "local_selection"
    if result.get("hosted_skipped") is not None:
        return "hosted_skipped"
    attempted = result.get("hosted_attempted") is True
    if not attempted:
        return "hosted_skipped"
    if isinstance(selected, str) and selected:
        if result.get("source") == "jev":
            return "hosted_selection"
        return "hosted_abstention"
    return "hosted_abstention"


def _safe_identifier(value: Any) -> str | None:
    if not isinstance(value, str) or any(marker in value for marker in _RECEIPT_FORBIDDEN_MARKERS):
        return None
    return receipt_state.safe_identifier(value)


def _safe_reason(value: Any) -> str | None:
    if not isinstance(value, str) or any(marker in value for marker in _RECEIPT_FORBIDDEN_MARKERS):
        return None
    return receipt_state.safe_reason(value)


def _result_value(result: Mapping[str, Any], name: str) -> Any:
    """Read hosted metadata without requiring callers to know its prefix."""
    prefixed = result.get(f"jev_{name}")
    return prefixed if prefixed is not None else result.get(name)


def build_routing_receipt(result: dict) -> dict:
    """Build a privacy-safe, typed routing receipt from a recommendation result.

    The receipt is operator-visible local evidence only. It exposes stable typed
    fields for every automatic-routing terminal state without carrying task text,
    candidate descriptions, conversation history, credentials, or provider
    exception text. It never implies a skill was loaded or a GUI action ran; the
    receipt is advisory and `verified` is always false.
    """
    if not isinstance(result, Mapping):
        result = {}
    raw_selected = result.get("selected")
    selected = _safe_identifier(raw_selected)
    if raw_selected is not None and selected is None:
        result = {
            **result,
            "selected": None,
            "source": "none",
            "hosted_attempted": False,
            "hosted_error": None,
            "hosted_skipped": "diagnostic_value_unavailable",
        }
    hosted_attempted = result.get("hosted_attempted") is True and result.get("cache_hit") is not True
    hosted_error = result.get("hosted_error")
    if hosted_error is not None and hosted_error not in HOSTED_ERROR_CODES:
        hosted_error = "transport_or_execution_failure"
    detail = result.get("hosted_error_detail")
    hosted_error_detail = (
        detail if type(detail) is str and detail in receipt_state.HOSTED_ERROR_DETAILS else "unknown"
    ) if hosted_error is not None else None
    terminal_state = _terminal_state(result)
    source = result.get("source") if result.get("source") in {"local", "jev", "none"} else "none"
    if result.get("cache_hit") is True:
        hosted_attempted = False
        hosted_error = None
        hosted_error_detail = None
        selected = _safe_identifier(result.get("selected"))
        hosted_skip_reason = "cache_hit"
        jev_model = None
        request_id = None
        request_count = 0
        latency_ms = 0.0
        total_latency_ms = 0.0
        total_usage: dict[str, float] = {}
    else:
        hosted_skip_reason = result.get("hosted_skipped")
        if hosted_skip_reason not in receipt_state.HOSTED_SKIP_REASONS:
            hosted_skip_reason = "diagnostic_value_unavailable" if terminal_state == "hosted_skipped" else None
        jev_model = _safe_identifier(_result_value(result, "model"))
        request_id = _safe_identifier(_result_value(result, "request_id"))
        request_count = receipt_state.nonnegative_int(_result_value(result, "request_count"))
        latency_ms = receipt_state.finite_nonnegative(_result_value(result, "latency_ms"))
        total_latency_ms = receipt_state.finite_nonnegative(
            _result_value(result, "total_latency_ms"), default=latency_ms
        )
        total_usage = receipt_state.safe_usage(
            _result_value(result, "total_usage") or _result_value(result, "usage") or {}
        )
    bypass_reason = "cache_hit" if result.get("cache_hit") is True else result.get("bypass_reason")
    if bypass_reason not in receipt_state.BYPASS_REASONS:
        bypass_reason = None
    input_chars = (
        receipt_state.nonnegative_int(result.get("input_chars")) if hosted_attempted else 0
    )
    hosted_succeeded = bool(
        hosted_attempted
        and hosted_error is None
        and terminal_state in {"hosted_selection", "hosted_abstention"}
    )
    identity = receipt_state.plugin_identity()

    return {
        "terminal_state": terminal_state,
        "source": source,
        "selected": selected,
        "hosted_attempted": hosted_attempted,
        "hosted_succeeded": hosted_succeeded,
        "hosted_error": hosted_error,
        "hosted_error_detail": hosted_error_detail,
        "hosted_skip_reason": hosted_skip_reason,
        "abstention_reason": _safe_reason(result.get("abstention_reason")),
        "jev_model": jev_model,
        "request_id": request_id,
        "request_count": request_count,
        "latency_ms": latency_ms,
        "total_latency_ms": total_latency_ms,
        "total_usage": total_usage,
        "candidate_count": receipt_state.nonnegative_int(result.get("candidate_count")),
        "offered_count": receipt_state.nonnegative_int(_result_value(result, "offered_count")),
        "excluded_count": receipt_state.nonnegative_int(_result_value(result, "excluded_count")),
        "shortlist_policy": _safe_identifier(_result_value(result, "shortlist_policy")),
        "bypass_reason": bypass_reason,
        "input_chars": input_chars,
        "verified": False,
        "advisory_only": True,
        "plugin_identity": identity,
        "source_sha": identity["source_sha"],
    }



def build_consumption_contract(
    *,
    delivery_status: str,
    adoption_status: str,
) -> dict[str, str]:
    """Return the first-class delivery/adoption/outcome record.

    Outcome is always ``unverified`` at the plugin boundary. Callers that want
    comparative improvement must measure it outside this receipt (for example
    paired end-to-end arms) and must not treat delivery as success.
    """
    if delivery_status not in receipt_state.DELIVERY_STATUSES:
        raise ValueError("delivery_status is invalid")
    if adoption_status not in receipt_state.ADOPTION_STATUSES:
        raise ValueError("adoption_status is invalid")
    if (delivery_status, adoption_status) not in receipt_state.VALID_DELIVERY_ADOPTION_PAIRS:
        raise ValueError("delivery/adoption pair is invalid")
    return {
        "delivery_status": delivery_status,
        "adoption_status": adoption_status,
        "outcome_status": "unverified",
    }


def _attach_consumption_contract(receipt: dict[str, Any], contract: Mapping[str, str]) -> dict[str, Any]:
    """Merge a validated consumption contract into a receipt copy."""
    updated = dict(receipt)
    updated.update(dict(contract))
    return updated


def _format_recommendation(name: str) -> str:
    return (
        "Advisory skill recommendation: consider the exact skill identifier "
        f"{json.dumps(name)} if it fits this request. The plugin did not load it. "
        "Mandatory skills, explicit instructions, safety controls, and the user's preferences take precedence."
    )


# Text-only explicit-skill intent (no catalog). Used to refuse early light bypass
# when the turn looks like "Use greeter …" / "/greeter" so discover still runs and
# `_explicit_skill_override` can honor a matching registry skill.
_EXPLICIT_SKILL_INTENT_RE = re.compile(
    r"(?:^|\s)/[a-z][\w.-]{1,63}(?:\s|$|[.,!?])"
    r"|"
    r"\b(?:use|load)\s+(?:the\s+)?[`'\"]?[a-z][\w:.-]*",
    re.IGNORECASE,
)


def _looks_like_explicit_skill_request(task: Any) -> bool:
    """True when the turn text looks like an explicit skill use/load/slash request.

    Catalog-free and conservative: any use/load target may be a skill name.
    The closed no-tools constraint is not an override. Errors force discovery.
    """
    try:
        text = _coerce_bounded_text(task, MAX_TASK_CHARS)
    except Exception:  # noqa: BLE001
        return True
    if not text:
        return False
    text = re.sub(r"\b(?:do\s+not|don't)\s+use\s+tools\b", "", text, flags=re.IGNORECASE)
    return _EXPLICIT_SKILL_INTENT_RE.search(text) is not None


def _explicit_skill_override(task: Any, candidates: Any) -> str | None:
    """Return an explicitly requested candidate, if the turn names one."""
    text = _coerce_bounded_text(task, MAX_TASK_CHARS).lower()
    for candidate in candidates or ():
        name = candidate.get("name") if isinstance(candidate, Mapping) else candidate
        if not isinstance(name, str) or not name:
            continue
        lowered_name = name.lower()
        if lowered_name not in text:
            continue
        escaped = re.escape(lowered_name)
        if re.search(rf"(?:^|\s)/{escaped}(?:\s|$)", text) or re.search(
            rf"\b(?:use|load)\s+(?:the\s+)?(?:skill\s+)?[`'\"]?{escaped}(?:[`'\"]|\b)",
            text,
        ):
            return name
    return None


def _candidate_name(candidate: Any) -> str | None:
    name = candidate.get("name") if isinstance(candidate, Mapping) else candidate
    return name if isinstance(name, str) and name else None


def _merge_override_candidate_pools(*pools: Any) -> tuple[Any, ...]:
    """Union candidate pools by exact name for explicit-override detection."""
    merged: list[Any] = []
    seen: set[str] = set()
    for pool in pools:
        for candidate in pool or ():
            name = _candidate_name(candidate)
            if name is None or name in seen:
                continue
            seen.add(name)
            merged.append(candidate)
    return tuple(merged)


def _any_candidate_adoptable(candidates: Any, mandatory_skills: Any) -> bool:
    """True when mandatory policy is empty or at least one candidate is allowed."""
    mandatory = discover_mandatory_skills(
        mandatory_skills if isinstance(mandatory_skills, (list, tuple)) else ()
    )
    if not mandatory:
        return True
    allowed = set(mandatory)
    for candidate in candidates or ():
        name = _candidate_name(candidate)
        if name is not None and name in allowed:
            return True
    return False


def discover_mandatory_skills(configured: Any = None) -> tuple[str, ...]:
    """Return exact mandatory skill identifiers without repairing names.

    An explicit configured list, including an empty list, wins. ``None``
    reads Hermes ``skills.always_load`` when that config is available and
    fails closed to no mandatory skills otherwise.
    """
    names: list[str] = []
    if isinstance(configured, (list, tuple)):
        for item in configured:
            if isinstance(item, str) and item:
                names.append(item)
        return tuple(names)
    if configured is not None:
        return ()
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
    except Exception:
        return ()
    skills = cfg.get("skills") if isinstance(cfg, dict) else None
    always_load = skills.get("always_load") if isinstance(skills, dict) else None
    if not isinstance(always_load, list):
        return ()
    for item in always_load:
        if isinstance(item, str) and item:
            names.append(item)
    return tuple(names)


def _mandatory_skill_conflict(selected: str, mandatory_skills: Any) -> bool:
    names = discover_mandatory_skills(mandatory_skills if isinstance(mandatory_skills, (list, tuple)) else ())
    return bool(names) and selected not in names


RECEIPT_PERSIST_FAILED_KEY = "receipt_persist_failed"


def _persist_receipt(receipt: dict[str, Any]) -> bool:
    """Save a receipt without ever breaking routing; return True when saved."""
    try:
        return receipt_state.store_latest_receipt(receipt) is True
    except Exception as exc:  # noqa: BLE001 -- receipt I/O must never break routing
        logger.warning(
            "Switchyard routing receipt was not saved (%s); skill routing continues.",
            type(exc).__name__,
        )
        return False


def _mark_persist_failure(metadata: dict[str, Any], failed: bool) -> None:
    """Report the final receipt save outcome for this turn in memory only."""
    if failed:
        metadata[RECEIPT_PERSIST_FAILED_KEY] = True
    else:
        metadata.pop(RECEIPT_PERSIST_FAILED_KEY, None)


def _format_preloaded_skill(name: str, content: str) -> str:
    """Mark a confirmed skill load in context; do not claim host-side dedup.

    The tool handler's repeat-view tracker does not observe direct skill_view()
    calls on supported Hermes hosts. This marker tells the model it already has
    the instructions while leaving manual refreshes possible.
    """
    literal = json.dumps(name, ensure_ascii=True)
    return (
        f"[Switchyard: skill {literal} already loaded for this turn]\n"
        "Hermes has already loaded this skill through skill_view. "
        f"Use the instructions below directly; do not call skill_view(name={literal}) "
        "again merely to load the same skill. Call it only if you need to refresh "
        "or verify the content.\n\n"
        f"{content}"
    )


def build_pre_llm_call_hook(
    *,
    enabled: bool = True,
    configured_candidates: Any = None,
    routing_mode: str | None = None,
    hosted_enabled: bool | None = None,
    hosted_mode: str = "always",
    public_or_sanitized_data_ack: bool = True,
    client_factory: Callable[[], Any] | None = None,
    cache_identity: Callable[[], Any] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    local_threshold: float = DEFAULT_LOCAL_THRESHOLD,
    local_margin: float = DEFAULT_LOCAL_MARGIN,
    cache_seconds: float = DEFAULT_CACHE_SECONDS,
    deadline_seconds: float = DEFAULT_AUTOMATIC_DEADLINE_SECONDS,
    consumer_mode: str = DEFAULT_CONSUMER_MODE,
    skill_loader: Callable[..., str] | None = None,
    mandatory_skills: Any = (),
    honor_no_skill_gate: bool = False,
    light_turn_bypass: bool = True,
    early_light_bypass_before_discover: bool = False,
    two_stage: TwoStageConfig | None = None,
    excerpt_loader: Callable[[str], Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> Callable[..., dict[str, Any] | None] | None:
    """Build a genuine Hermes ``pre_llm_call`` callback, or disable it."""
    if enabled is not True:
        return None
    if consumer_mode not in {"advisory", "load"}:
        raise ValueError("consumer_mode must be advisory or load")
    if consumer_mode == "load" and skill_loader is None:
        raise ValueError("load consumer mode requires a skill_loader")
    active_skill_loader = skill_loader
    try:
        recommender = AutomaticSkillRecommender(
            configured_candidates=configured_candidates,
            routing_mode=routing_mode,
            hosted_enabled=hosted_enabled,
            hosted_mode=hosted_mode,
            public_or_sanitized_data_ack=public_or_sanitized_data_ack,
            client_factory=client_factory,
            cache_identity=cache_identity,
            cancel_check=cancel_check,
            local_threshold=local_threshold,
            local_margin=local_margin,
            cache_seconds=cache_seconds,
            deadline_seconds=deadline_seconds,
            adoption_capable=(consumer_mode == "load"),
            honor_no_skill_gate=honor_no_skill_gate,
            light_turn_bypass=light_turn_bypass,
            two_stage=two_stage,
            excerpt_loader=excerpt_loader,
        )
    except (TypeError, ValueError) as exc:
        logger.warning("automatic skill recommendation disabled by invalid configuration: %s", type(exc).__name__)
        return None

    # Default OFF: preserve discover-then-recommend. When ON with light_turn_bypass,
    # a text-only probe may skip catalog discover for light turns (fail-open).
    early_before_discover = early_light_bypass_before_discover is True
    configured = bool(recommender.configured_candidates)
    consumed_turns: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()

    def on_pre_llm_call(
        *,
        user_message: Any = None,
        conversation_history: Any = None,
        turn_egress_policy: Any = None,
        egress_policy: Any = None,
        session_id: Any = None,
        turn_id: Any = None,
        platform: Any = None,
        **_: Any,
    ) -> dict[str, Any] | None:
        def record_history(receipt: dict[str, Any]) -> None:
            """Retain one sanitized turn record in the bounded history."""
            receipt_history.record_turn_receipt(
                receipt, session_id=session_id, turn_id=turn_id, platform=platform
            )

        turn_key = (
            (str(session_id), str(turn_id))
            if isinstance(session_id, str) and session_id and isinstance(turn_id, str) and turn_id
            else None
        )
        if turn_key is not None and turn_key in consumed_turns:
            return dict(consumed_turns[turn_key])
        # Non-interactive turns (API server, cron, batch, webhook, Kanban
        # worker) skip routing before discovery, scanning, or any request.
        gate = two_stage if two_stage is not None else TwoStageConfig()
        route_turn, platform_reason = platform_decision(
            platform,
            gate,
            kanban_worker=detect_kanban_worker(environ if environ is not None else os.environ),
        )
        if not route_turn:
            result = {
                "status": "abstained",
                "selected": None,
                "source": "none",
                "abstention_reason": platform_reason,
                "routing_mode": recommender.routing_mode,
                "routing_status": "hosted_skipped",
                "routing_reason": platform_reason,
                "hosted_attempted": False,
                "hosted_skipped": platform_reason,
                "candidate_count": 0,
                "candidates_considered": [],
                "cache_hit": False,
                "request_count": 0,
            }
            contract = build_consumption_contract(
                delivery_status="not_delivered",
                adoption_status="not_applicable",
            )
            receipt = _attach_consumption_contract(build_routing_receipt(result), contract)
            recommender.last_receipt = receipt
            persist_failed = not _persist_receipt(receipt)
            record_history(receipt)
            metadata = redacted_routing_metadata(result)
            _mark_persist_failure(metadata, persist_failed)
            metadata["skill_recommendation"] = {
                "status": "platform_skipped",
                "selected": None,
                "source": "none",
                "loaded_once": False,
                **contract,
            }
            setattr(on_pre_llm_call, "last_result", dict(result))
            setattr(on_pre_llm_call, "last_receipt", dict(receipt))
            setattr(on_pre_llm_call, "last_metadata", dict(metadata))
            setattr(on_pre_llm_call, "last_routing_metadata", dict(metadata))
            response = {"metadata": metadata}
            if turn_key is not None:
                consumed_turns[turn_key] = dict(response)
                while len(consumed_turns) > DEFAULT_CACHE_SIZE:
                    consumed_turns.popitem(last=False)
            return response
        # Hermes' conversation_history does not include the cached system prompt
        # that advertises skills. Discover the active profile registry directly.
        # Light-turn bypass runs inside recommend() *after* routing_mode==off and
        # *after* the explicit-skill override below, so disabled-mode and
        # explicit-override receipts keep their established precedence.
        # Optional early probe (flag default OFF) may skip discover when the same
        # text-only light predicate already fires and the turn does not look like an
        # explicit skill use/load; fail-open on probe errors.
        del conversation_history  # local-only input; never part of an egress payload
        if (
            early_before_discover
            and recommender.light_turn_bypass
            and recommender.routing_mode != "off"
        ):
            early_reason = None
            try:
                task_text = _coerce_text(user_message)
                if task_text:
                    early_reason = _hosted_skill_bypass_reason(task_text)
            except Exception:  # noqa: BLE001 -- fail open to discover path
                early_reason = None
            if early_reason is not None and not _looks_like_explicit_skill_request(
                user_message
            ):
                # Same light path as recommend(); empty candidates never reached.
                # Explicit-use phrasing falls through so discover + override run.
                result = recommender.recommend(
                    user_message,
                    candidates=(),
                    candidates_from_prompt=False,
                    turn_egress_policy=turn_egress_policy,
                    egress_policy=egress_policy,
                )
                setattr(on_pre_llm_call, "last_result", dict(result))
                setattr(
                    on_pre_llm_call, "last_receipt", dict(recommender.last_receipt or {})
                )
                metadata = redacted_routing_metadata(result)
                setattr(on_pre_llm_call, "last_metadata", dict(metadata))
                setattr(on_pre_llm_call, "last_routing_metadata", dict(metadata))
                contract = build_consumption_contract(
                    delivery_status="not_delivered",
                    adoption_status="not_applicable",
                )
                receipt = _attach_consumption_contract(
                    dict(recommender.last_receipt or {}), contract
                )
                recommender.last_receipt = receipt
                persist_failed = not _persist_receipt(receipt)
                record_history(receipt)
                _mark_persist_failure(metadata, persist_failed)
                setattr(on_pre_llm_call, "last_receipt", dict(receipt))
                metadata["skill_recommendation"] = {
                    "status": "abstained",
                    "selected": None,
                    "source": result.get("source", "none"),
                    "loaded_once": False,
                    **contract,
                }
                for name in ("last_metadata", "last_routing_metadata"):
                    snapshot = dict(getattr(on_pre_llm_call, name, None) or {})
                    _mark_persist_failure(
                        snapshot, bool(metadata.get(RECEIPT_PERSIST_FAILED_KEY))
                    )
                    setattr(on_pre_llm_call, name, snapshot)
                response = {"metadata": metadata}
                if turn_key is not None:
                    consumed_turns[turn_key] = dict(response)
                    while len(consumed_turns) > DEFAULT_CACHE_SIZE:
                        consumed_turns.popitem(last=False)
                return response
        catalog_candidates = (
            ()
            if configured or recommender.routing_mode == "off"
            else discover_available_skill_candidates()
        )
        candidates = recommender.configured_candidates if configured else catalog_candidates
        # Explicit skill instructions are detected against the full active
        # registry, not only the configured routing subset, so a user
        # instruction like "Use network-printer-operations" still zero-requests
        # even when routing is limited to docker-management.
        registry_for_override = (
            catalog_candidates
            if catalog_candidates
            else discover_available_skill_candidates()
        )
        override_pool = _merge_override_candidate_pools(candidates, registry_for_override)
        # Explicit skill instructions win before any local/hosted selection so a
        # determined turn never pays for a hosted Jev request.
        override = _explicit_skill_override(user_message, override_pool)
        if override is not None:
            result = {
                "status": "abstained",
                "selected": None,
                "source": "none",
                "abstention_reason": "explicit_override",
                "routing_mode": recommender.routing_mode,
                "routing_status": "hosted_skipped",
                "routing_reason": "explicit_override",
                "hosted_attempted": False,
                "hosted_skipped": "explicit_override",
                "candidate_count": len(candidates) if isinstance(candidates, (list, tuple)) else 0,
                "candidates_considered": [],
                "cache_hit": False,
                "request_count": 0,
            }
            contract = build_consumption_contract(
                delivery_status="skipped",
                adoption_status="suppressed",
            )
            receipt = _attach_consumption_contract(build_routing_receipt(result), contract)
            if consumer_mode == "load":
                receipt.update(
                    {
                        "consumer_status": "explicit_override",
                        "loaded_skill": None,
                        "loaded_source": None,
                        "skill_load_verified": False,
                        "advisory_only": True,
                    }
                )
            recommender.last_receipt = receipt
            persist_failed = not _persist_receipt(receipt)
            record_history(receipt)
            metadata = redacted_routing_metadata(result)
            _mark_persist_failure(metadata, persist_failed)
            metadata["skill_recommendation"] = {
                "status": "explicit_override",
                "selected": None,
                "explicit_skill": override,
                "source": "none",
                "loaded_once": False,
                **contract,
            }
            setattr(on_pre_llm_call, "last_result", dict(result))
            setattr(on_pre_llm_call, "last_receipt", dict(receipt))
            setattr(on_pre_llm_call, "last_metadata", dict(metadata))
            setattr(on_pre_llm_call, "last_routing_metadata", dict(metadata))
            response = {"metadata": metadata}
            if turn_key is not None:
                consumed_turns[turn_key] = dict(response)
                while len(consumed_turns) > DEFAULT_CACHE_SIZE:
                    consumed_turns.popitem(last=False)
            return response

        # In load mode, authorize hosted work only when at least one routing
        # candidate remains adoptable under mandatory-skill policy. Otherwise
        # hosted would pay before the later mandatory_conflict suppress path.
        if consumer_mode == "load":
            recommender.adoption_capable = _any_candidate_adoptable(
                candidates, mandatory_skills
            )
        result = recommender.recommend(
            user_message,
            candidates=catalog_candidates,
            candidates_from_prompt=False,
            turn_egress_policy=turn_egress_policy,
            egress_policy=egress_policy,
        )
        setattr(on_pre_llm_call, "last_result", dict(result))
        setattr(on_pre_llm_call, "last_receipt", dict(recommender.last_receipt or {}))
        metadata = redacted_routing_metadata(result)
        setattr(on_pre_llm_call, "last_metadata", dict(metadata))
        setattr(on_pre_llm_call, "last_routing_metadata", dict(metadata))
        selected = result.get("selected")
        if not isinstance(selected, str) or not selected:
            contract = build_consumption_contract(
                delivery_status="not_delivered",
                adoption_status="not_applicable",
            )
            receipt = _attach_consumption_contract(dict(recommender.last_receipt or {}), contract)
            recommender.last_receipt = receipt
            persist_failed = not _persist_receipt(receipt)
            record_history(receipt)
            _mark_persist_failure(metadata, persist_failed)
            setattr(on_pre_llm_call, "last_receipt", dict(receipt))
            metadata["skill_recommendation"] = {
                "status": "abstained",
                "selected": None,
                "source": result.get("source", "none"),
                "loaded_once": False,
                **contract,
            }
            response = {"metadata": metadata}
        elif consumer_mode == "advisory":
            contract = build_consumption_contract(
                delivery_status="delivered",
                adoption_status="not_adopted",
            )
            receipt = _attach_consumption_contract(dict(recommender.last_receipt or {}), contract)
            recommender.last_receipt = receipt
            persist_failed = not _persist_receipt(receipt)
            record_history(receipt)
            _mark_persist_failure(metadata, persist_failed)
            setattr(on_pre_llm_call, "last_receipt", dict(receipt))
            metadata["skill_recommendation"] = {
                "status": "advisory",
                "selected": selected,
                "source": result.get("source", "none"),
                "loaded_once": False,
                **contract,
            }
            response = {"context": _format_recommendation(selected), "metadata": metadata}
        else:
            if _mandatory_skill_conflict(selected, mandatory_skills):
                status, loaded_context = "mandatory_conflict", None
                contract = build_consumption_contract(
                    delivery_status="skipped",
                    adoption_status="suppressed",
                )
            else:
                try:
                    if active_skill_loader is None:
                        raise RuntimeError("skill loader is unavailable")
                    loaded_context = active_skill_loader(selected, task_id=session_id)
                    if not isinstance(loaded_context, str) or not loaded_context.strip():
                        raise ValueError("skill loader returned no content")
                    status = "loaded"
                    contract = build_consumption_contract(
                        delivery_status="delivered",
                        adoption_status="adopted",
                    )
                except Exception:  # noqa: BLE001 -- consumer fails closed to advisory context
                    logger.warning("automatic skill consumer rejected %s", selected, exc_info=True)
                    status, loaded_context = "load_failed", None
                    contract = build_consumption_contract(
                        delivery_status="delivered",
                        adoption_status="not_adopted",
                    )
            loaded_once = status == "loaded"
            metadata["skill_recommendation"] = {
                "status": status,
                "selected": selected,
                "source": result.get("source", "none"),
                "loaded_once": loaded_once,
                **contract,
            }
            receipt = dict(recommender.last_receipt or {})
            receipt.update(
                {
                    "consumer_status": status,
                    "loaded_skill": selected if loaded_once else None,
                    "loaded_source": result.get("source", "none") if loaded_once else None,
                    "skill_load_verified": loaded_once,
                    "advisory_only": not loaded_once,
                }
            )
            receipt = _attach_consumption_contract(receipt, contract)
            recommender.last_receipt = receipt
            persist_failed = not _persist_receipt(receipt)
            record_history(receipt)
            _mark_persist_failure(metadata, persist_failed)
            setattr(on_pre_llm_call, "last_receipt", dict(receipt))
            # Receipt must match observable delivery: skipped/suppressed
            # mandatory conflicts omit recommendation context (same as
            # explicit_override). Advisory fallback is only for load_failed.
            if status == "mandatory_conflict":
                response = {"metadata": metadata}
            else:
                response = {
                    "context": (
                        _format_preloaded_skill(selected, loaded_context)
                        if loaded_once
                        else _format_recommendation(selected)
                    ),
                    "metadata": metadata,
                }
        # The routing snapshots were taken before the final receipt save.
        # Refresh only the save outcome so they report it accurately.
        for name in ("last_metadata", "last_routing_metadata"):
            snapshot = dict(getattr(on_pre_llm_call, name, None) or {})
            _mark_persist_failure(snapshot, bool(metadata.get(RECEIPT_PERSIST_FAILED_KEY)))
            setattr(on_pre_llm_call, name, snapshot)
        if turn_key is not None:
            consumed_turns[turn_key] = dict(response)
            while len(consumed_turns) > DEFAULT_CACHE_SIZE:
                consumed_turns.popitem(last=False)
        return response

    return on_pre_llm_call
