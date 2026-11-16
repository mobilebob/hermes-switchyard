"""Verify saved first-party billing evidence, not a caller's approval boolean."""
from __future__ import annotations

import json
from pathlib import Path
import re

from live_transport import Blocked, sha, TOKEN_CEILING, RATE_MICRO_USD_PER_MILLION, RESERVE_MICRO_USD, MAX_REQUESTS, MAX_MICRO_USD

BILLING_URL = "https://help.typesafe.ai/articles/5187202169-how-much-does-jev-cost"
MODELS_URL = "https://docs.typesafe.ai/models.md"


def verify_billing(directory: Path) -> dict:
    records = json.loads((directory / "billing-provenance.json").read_text())
    indexed = {r.get("name"): r for r in records}
    texts = {}
    for name, url in (("billing", BILLING_URL), ("models", MODELS_URL)):
        record = indexed.get(name, {})
        raw = (directory / (name + "-source")).read_bytes()
        if record.get("url") != url or record.get("resolved_url") != url or record.get("sha256") != sha(raw):
            raise Blocked("billing_source_identity_or_hash_mismatch")
        text = (directory / (name + ".txt")).read_text()
        # Derived text is evidence too: every significant clause must actually
        # occur in the saved source, not just in a separately editable summary.
        source = raw.decode(errors="strict")
        normalized_source = re.sub(r"<[^>]+>", " ", source)
        normalized_source = normalized_source.replace("\\$", "$")
        normalized_source = re.sub(r"\s+", " ", normalized_source).lower()
        normalized_text = re.sub(r"\s+", " ", text.replace("\\$", "$")).lower()
        texts[name] = normalized_text
        clauses = (["$0.042 per million input tokens", "output tokens are free", "same pricing applies to every account",
                    "no per-request, failed-request or minimum fees"] if name == "billing" else
                   ["jev-1.14.0", "$0.042", "64k per-request", "32k per-question", "ingests the `state` once",
                    "the `state` plus all questions combined", "long context is off by default",
                    "typesafe-danger-zone-long-context", "262k tokens"])
        for clause in clauses:
            if clause not in normalized_text or clause not in normalized_source:
                raise Blocked("missing_verified_billing_clause")
    upper = (TOKEN_CEILING * RATE_MICRO_USD_PER_MILLION + 999_999) // 1_000_000
    if upper > RESERVE_MICRO_USD or MAX_REQUESTS * RESERVE_MICRO_USD > MAX_MICRO_USD:
        raise Blocked("billing_reservations_insufficient")
    return {"sources": [{"url": indexed[n]["url"], "sha256": indexed[n]["sha256"],
                         "retrieved_at": indexed[n]["retrieved_at"]} for n in ("billing", "models")],
            "account_universal_tariff": True, "input_usd_per_million": "0.042", "output_free": True,
            "extra_request_failed_request_minimum_fees": False,
            "default_request_context": "64k state plus all questions; state ingested once",
            "extended_context_header_sent": False, "additional_billable_services": [],
            "conservative_billable_token_ceiling": TOKEN_CEILING,
            "conservative_per_request_upper_micro_usd": upper,
            "per_request_reservation_micro_usd": RESERVE_MICRO_USD,
            "all_request_reservations_micro_usd": MAX_REQUESTS * RESERVE_MICRO_USD,
            "authorized_micro_usd": MAX_MICRO_USD,
            "note": "Conservative token ceiling exceeds default context. Fixed transport sends no long-context header, media, model execution or other service request."}
