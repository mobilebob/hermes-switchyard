"""Report Stage A live prerequisites without contacting any provider.

Inventory is an operator-supplied read-only discovery snapshot, not permission.
No option in this command installs plugins, loads credentials or dispatches calls.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from live_transport import MODEL, MAX_REQUESTS, MAX_MICRO_USD, RESERVE_MICRO_USD, authentication_ready_or_bootstrap


def assess(inventory: dict) -> dict:
    missing = []
    if inventory.get("classifier_provider") != "typesafe" or not inventory.get("credential_present"):
        missing.append("direct_typesafe_credential_not_available")
    if inventory.get("classifier_model") != MODEL or not authentication_ready_or_bootstrap(inventory):
        missing.append("authenticated_typesafe_account_and_exact_model_not_verified")
    candidates = inventory.get("approved_candidate_ids")
    if (not isinstance(candidates, list) or len(candidates) < 2 or
            any(type(c) is not str or not c for c in candidates) or
            len(set(candidates)) != len(candidates) or
            inventory.get("candidate_account_verified") is not True or
            inventory.get("candidate_capabilities_verified") is not True or
            inventory.get("allowlist_owner_approved") is not True):
        missing.append("explicit_account_bound_same_provider_model_allowlist_not_verified")
    elif any(not isinstance(c, str) or not c.startswith(str(inventory.get("model_provider")) + ":") for c in candidates):
        missing.append("candidate_provider_boundary_mismatch")
    if inventory.get("public_payloads_verified") is not True:
        missing.append("exact_native_request_bodies_not_reviewed_for_public_egress")
    if inventory.get("price_ceiling_verified") is not True:
        missing.append("account_applicable_price_and_billable_token_ceiling_not_verified")
    return {
        "status": "prepared_live_blocked" if missing else "prerequisites_reported_requires_verified_driver",
        "missing_prerequisites": missing,
        "authentication_status": "verified" if inventory.get("account_verified") is True else (
            "pending_first_reserved_request" if authentication_ready_or_bootstrap(inventory) else "unverified"),
        "owner_authorization": {"max_external_jev_requests": MAX_REQUESTS,
            "max_micro_usd": MAX_MICRO_USD, "max_main_model_evaluation_requests": 0,
            "retries": 0, "model_switching": False},
        "conservative_policy": {"reserve_micro_usd_per_dispatch": RESERVE_MICRO_USD,
            "max_reservations_micro_usd": MAX_REQUESTS * RESERVE_MICRO_USD,
            "uncertain_requests_keep_both_reservations": True},
        "real_jev_requests": 0, "real_main_model_evaluation_requests": 0,
        "reserved_requests": 0, "reserved_micro_usd": 0, "billed_cost_micro_usd": 0,
        "measured_shadow_observations": 0, "promotion_authorized": False,
        "accounting_scope": "this non-dispatching preflight invocation, not an audit of another ledger",
        "note": "Public pricing and cached model names alone do not prove account access or model approval. No dispatch path in this command.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        raw = json.loads(args.inventory.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("inventory must be an object")
        report = assess(raw)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except (ValueError, OSError) as exc:
        print(f"Preflight error: {type(exc).__name__}")
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["missing_prerequisites"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
