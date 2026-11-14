"""Bounded local warning screen for instructions embedded in retrieved text.

This is defense in depth, not a trust classifier or an authorization decision.
No text, matched span, URL, or credential is included in its receipts.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote_plus

MAX_TEXT = 96_000

# Keep bare overrides at an operational boundary, not anywhere in prose. Literal
# escaped newlines occur in retrieved JSON text; no general unescaping/eval is used.
# Do not consume CR/LF at every line start: a whitespace-only page would make
# repeated failed boundary matches rescan the suffix quadratically.
_BOUNDARY = r"(?:(?:^|\r|\\[nr])[^\S\r\n]*|(?<=[.!?;])[^\S\r\n]+)"
_AGENT = r"(?:assistant|chatgpt|AI\s+agent|language\s+model)"
_ACTION = (
    r"(?:ignore|disregard|override|disable|bypass|replace|delete|discard|forget|"
    r"call|run|execute|invoke|read|open|visit|send|upload|post|transmit|paste|"
    r"reveal|disclose|report|return|answer|say)"
)
_AGENT_COMMAND = (
    rf"\b{_AGENT}(?:\s+(?:action|directive|instructions?|task)"
    r"(?:\s+(?:needed|required))?)?\s*[:,!]\s*(?:please\s+)?"
    rf"{_ACTION}\b"
)
_PATTERNS = (
    (
        "instruction_override",
        re.compile(
            rf"(?im){_BOUNDARY}(?:attention[,:]?\s*)?(?:ignore|disregard|override)\s+"
            r"(?:all\s+)?(?:previous|prior|earlier|system|developer|safety)\s+"
            r"(?:instructions?|prompts?|rules?|messages?|polic(?:y|ies))\b"
        ),
    ),
    (
        "role_spoof",
        re.compile(
            # Structural role tokens need no line start, including [im_start].
            r"(?im)(?:<\|(?:im_start|start_header_id)\|>|\[im_start\])"
            r"\s*(?:system|developer)\b|"
            rf"{_BOUNDARY}(?:<(?:system|developer)>|"
            r"\[(?:system|developer)\]\s*(?:ignore|you|override)|"
            r"(?:system|developer)\s*:\s*(?:ignore|disregard|override|you must))|"
            rf"<(?:system|developer)>\s*{_ACTION}\b"
        ),
    ),
    ("agent_directive", re.compile(rf"(?i){_AGENT_COMMAND}")),
    (
        "exfil_instruction",
        re.compile(
            r"(?i)\b(?:before\s+(?:answering|responding)|to\s+(?:answer|respond))"
            r"\s*[,;:]\s*(?:open|visit|send|upload|post|transmit|paste)\b"
            r"[^\r\n]{0,500}\b(?:your\s+(?:current\s+|hidden\s+)?instructions|"
            r"(?:hidden\s+)?system\s+prompts?|internal\s+(?:tool\s+)?state|"
            r"API\s+keys?|credentials?|private\s+(?:files?|settings))\b"
        ),
    ),
    (
        "policy_spoof",
        re.compile(
            r"(?i)\b(?:(?:rules?|polic(?:y|ies)|instructions?)\s+for\s+"
            r"(?:AI\s+readers|(?:automated\s+)?assistants?|AI\s+agents?|"
            r"language\s+models?)|(?:site|website|page|source)\s+policy)"
            r"\s*:\s*(?:you\s+must\s+)?"
            r"(?:ignore|disregard|override|disable|bypass|supersede)\b"
        ),
    ),
    (
        "citation_spoof",
        re.compile(
            r"(?i)\b(?:citation|evidence|source)\s+(?:override|directive|instruction)"
            r"\s*:\s*(?:cite|present|treat|describe|report)\b"
        ),
    ),
    (
        "html_comment_directive",
        re.compile(rf"(?i)<!--\s*{_AGENT_COMMAND}"),
    ),
    (
        "structured_instruction",
        re.compile(
            r'''(?i)\b(?:assistant|agent|system|developer)[_-]instructions?'''
            r'''(?:\\?["'])?\s*:\s*(?:\\?["'])?\s*'''
            rf"(?:please\s+)?{_ACTION}\b"
        ),
    ),
    (
        "multilingual_override",
        re.compile(
            r"(?i)\b(?:(?:instrucci[oó]n(?:es)?|[oó]rdenes?)\s+para\s+"
            r"(?:el|la|los|las)\s+(?:asistentes?|agentes?)|"
            r"para\s+(?:el|la)\s+asistente|asistente)\s*[:,!]\s*"
            r"(?:ignora|omite|desobedece|olvida|descarta|anula|no\s+sigas)\b"
        ),
    ),
)


def screen_text(text: Any) -> tuple[str, ...]:
    """Return lexical warnings, not a clean/safe verdict, over bounded text.

    NFKC and format-control removal are shared by every caller of this screen.
    Both raw input and normalized expansion are capped before regex scanning.
    We never suppress quoted spans based on a page's claims: the narrow bare
    override boundary preserves ordinary inline quotations, but explicit agent
    commands/role tokens inside training examples can still warn. Arbitrary
    encodings, homoglyphs, implicit intent and other languages remain bypasses.
    Warnings cannot authorize tools or replace an owned enforcement boundary.
    """
    if not isinstance(text, str):
        return ()
    if len(text) > MAX_TEXT:
        return ("screen_limit",)
    normalized = unicodedata.normalize("NFKC", text)
    if len(normalized) > MAX_TEXT:
        return ("screen_limit",)
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Cf")
    return tuple(name for name, pattern in _PATTERNS if pattern.search(normalized))


def card_anchors(card: Mapping[str, Any]) -> Any:
    """Shared alias precedence for screening and candidate normalization."""
    for key in ("match_anchors", "match_message_ids", "match_message_id"):
        value = card.get(key)
        if value is not None:
            return value
    return None


def anchor_preview(anchor: Mapping[str, Any]) -> Any:
    """Return the exact raw preview that normalization will consume."""
    return anchor.get("preview") or anchor.get("snippet") or ""


def screen_card(card: Any) -> tuple[str, ...]:
    if not isinstance(card, Mapping):
        return ()  # Existing schema validation owns malformed objects.
    fields = [card.get("title"), card.get("snippet")]
    anchors = card_anchors(card)
    if isinstance(anchors, (list, tuple)):
        if len(anchors) > 32:
            return ("screen_limit",)
        fields.extend(anchor_preview(a) for a in anchors if isinstance(a, Mapping))
    if sum(len(field) for field in fields if isinstance(field, str)) > MAX_TEXT:
        return ("screen_limit",)
    return tuple(sorted({reason for field in fields for reason in screen_text(field)}))


def screen_shortlist(candidates: Any) -> tuple[Any, dict[str, Any]]:
    """Keep clean cards, including on provider failure. Do not mutate input."""
    if not isinstance(candidates, (list, tuple)) or len(candidates) > 32:
        return candidates, {"screened": False, "withheld": 0, "reasons": []}
    kept, reasons = [], set()
    for card in candidates:
        found = screen_card(card)
        if found:
            reasons.update(found)
        else:
            kept.append(card)
    return kept, {
        "screened": True,
        "withheld": len(candidates) - len(kept),
        "reasons": sorted(reasons),
    }


def screen_url(value: Any) -> tuple[str, ...]:
    """Screen URL components locally, including three common decoding layers."""
    if not isinstance(value, str):
        return ()
    if len(value) > MAX_TEXT:
        return ("screen_limit",)
    reasons = set()
    current = value
    for attempt in range(4):
        for part in (current, *re.split(r"[/?&#=]", current)):
            reasons.update(screen_text(part))
        if attempt == 3:
            break
        decoded = unquote_plus(current)
        if decoded == current:
            break
        current = decoded
    return tuple(sorted(reasons))


def screen_page(page: Mapping[str, Any]) -> tuple[str, ...]:
    fields = [page.get("title"), page.get("text")]
    urls = [page.get("url")]
    elements = page.get("elements")
    if isinstance(elements, list):
        if len(elements) > 2000:
            return ("screen_limit",)
        fields.extend(e.get("label") for e in elements if isinstance(e, Mapping))
        urls.extend(e.get("href") for e in elements if isinstance(e, Mapping))
    if sum(len(value) for value in fields + urls if isinstance(value, str)) > MAX_TEXT:
        return ("screen_limit",)
    reasons = {reason for field in fields for reason in screen_text(field)}
    reasons.update(reason for url in urls for reason in screen_url(url))
    return tuple(sorted(reasons))
