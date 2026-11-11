"""Isolated TypeSafe shadow transport, with durable pre-dispatch reservations.

No CLI, credential discovery, SDK retries, provider fallback, or model execution.
An operator must supply independently verified prerequisites and exact approved
public payloads. A cache entry is not model/account/price authorization.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import sqlite3
import ssl
import time

MODEL = "jev-1.14.0"
HOST = "api.typesafe.ai"
PATH = "/v1/systemone"
MAX_REQUESTS = 24
MAX_MICRO_USD = 500_000
RESERVE_MICRO_USD = 20_000
# Published $0.042/M input tokens, free output. Deliberately use a larger
# context bound than the default 64k; no images/long-context headers are sent.
TOKEN_CEILING = 262_144
RATE_MICRO_USD_PER_MILLION = 42_000
MAX_BODY = 16_000
MAX_QUESTIONS = 3
MAX_RESPONSE = 1_000_000
ARMS = ("switchyard", "jev-skills")
PINS = {"switchyard": "cc45394c6c04627f6e62736f987fceb84117a6d9",
        "jev-skills": "8316358bf60e98a21477582af829ef11c9cf52fb"}


class Blocked(ValueError):
    """A prerequisite/payload/budget prevents dispatch; no retry is authorized."""


class Uncertain(RuntimeError):
    """Dispatch may have happened; both reservations remain consumed."""


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def strict_loads(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Blocked("duplicate_json_key")
            result[key] = value
        return result
    def nonfinite(_):
        raise Blocked("nonfinite_json")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)


def authentication_ready_or_bootstrap(proof: dict) -> bool:
    return proof.get("account_verified") is True or (
        proof.get("credential_reuse_authorized") is True and
        proof.get("authenticate_on_first_reserved_request") is True)


def validate_prerequisites(proof: dict) -> None:
    required = ("model_allowlist_verified", "public_payloads_verified", "price_ceiling_verified")
    if isinstance(proof, dict) and proof.get("owner_waived_hard_spend_ceiling") is True:
        raise Blocked("spend_ceiling_waiver_not_allowed")
    if (not isinstance(proof, dict) or any(proof.get(k) is not True for k in required)
            or not authentication_ready_or_bootstrap(proof)):
        raise Blocked("unverified_live_prerequisites")
    for key in ("account_reference", "allowlist_reference", "payload_review_reference", "pricing_reference"):
        if type(proof.get(key)) is not str or not proof[key].strip():
            raise Blocked("missing_verification_reference")
    fingerprint = proof.get("credential_sha256")
    if type(fingerprint) is not str or len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
        raise Blocked("missing_credential_binding")
    if proof.get("provider") != "typesafe" or proof.get("model") != MODEL:
        raise Blocked("unapproved_classifier_route")
    # Must be an account-applicable upper bound, not just a public price estimate.
    if (type(proof.get("rate_micro_usd_per_million")) is not int or
            not 0 <= proof["rate_micro_usd_per_million"] <= RATE_MICRO_USD_PER_MILLION or
            proof.get("output_free") is not True or proof.get("no_other_fees") is not True or
            proof.get("billable_input_token_ceiling") != TOKEN_CEILING):
        raise Blocked("unverified_billing_ceiling")


def body_for(payload: dict) -> bytes:
    if not isinstance(payload, dict) or set(payload) != {"model", "state", "questions"} or payload["model"] != MODEL:
        raise Blocked("unapproved_request_shape_or_model")
    questions = payload["questions"]
    if not isinstance(questions, dict) or not 1 <= len(questions) <= MAX_QUESTIONS:
        raise Blocked("unbounded_questions")
    for question in questions.values():
        if not isinstance(question, dict) or question.get("type") not in ("noul", "choice", "score"):
            raise Blocked("unsupported_question")
        if set(question) - {"type", "instructions", "criteria"}:
            raise Blocked("unapproved_question_fields")
    forbidden = {"assets", "image", "images", "image_url", "audio", "video", "base64",
                 "authorization", "api_key", "access_token", "refresh_token", "password", "cookies"}
    def inspect(value):
        if isinstance(value, dict):
            if any(str(k).lower() in forbidden for k in value):
                raise Blocked("prohibited_outbound_field")
            for item in value.values():
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)
    inspect(payload)
    body = canonical(payload)
    if len(body) > MAX_BODY:
        raise Blocked("oversized_outbound_payload")
    return body


class ApprovedPayloads:
    """Exact whole-request allowlist, bound to frozen cases, source and review.

    Construct only from trusted reviewed artifacts. This class validates content
    integrity, not the identity/authority of whoever wrote an approval artifact.
    Privacy policy is checked independently before even an approved hash is used.
    """
    def __init__(self, cases: dict, entries: list[dict], proof: dict):
        validate_prerequisites(proof)
        self.account_reference = proof["account_reference"]
        self.credential_sha256 = proof["credential_sha256"]
        self.proof_hash = sha(canonical(proof))
        frozen = strict_loads(Path(__file__).with_name("cases.json").read_bytes())
        if cases != {c["id"]: c for c in frozen["cases"]}:
            raise Blocked("noncanonical_cases_or_policy")
        self.cases = {c["id"]: c for c in frozen["cases"]}
        self.entries = {}
        for entry in entries:
            key = (entry.get("case_id"), entry.get("arm"))
            if key[0] not in cases or key[1] not in ARMS or key in self.entries:
                raise Blocked("invalid_or_duplicate_payload_identity")
            case = cases[key[0]]
            if case["egress_limit"] != "redacted_text":
                raise Blocked("case_forbids_text_egress")
            if entry.get("source_sha") != PINS[key[1]] or entry.get("task_sha256") != sha(case["task"].encode()):
                raise Blocked("unverified_payload_provenance")
            body = body_for(entry["payload"])
            state = entry["payload"]["state"]
            task_field = "task" if key[1] == "switchyard" else "user_turn"
            if not isinstance(state, dict) or state.get(task_field) != case["task"]:
                raise Blocked("outbound_task_mismatch")
            if entry.get("body_sha256") != sha(body):
                raise Blocked("outbound_review_hash_mismatch")
            self.entries[key] = body
        if not self.entries:
            raise Blocked("no_approved_payloads")
        self.binding = sha(canonical({"proof": self.proof_hash, "entries": [
            [*key, sha(body)] for key, body in sorted(self.entries.items())]}))

    def validate(self, case_id: str, arm: str, payload: dict) -> bytes:
        if case_id not in self.cases or arm not in ARMS:
            raise Blocked("unknown_case_or_arm")
        if self.cases[case_id]["egress_limit"] != "redacted_text":
            raise Blocked("case_forbids_text_egress")
        body = body_for(payload)
        if self.entries.get((case_id, arm)) != body:
            raise Blocked("payload_not_exactly_reviewed")
        return body


class BudgetLedger:
    """One durable authorization ledger shared by both arms and all processes.

    Creation is explicit/exclusive; reopening never silently creates an empty
    budget. Never refund reservations, including after errors or uncertain sends.
    Keep this file outside temporary homes; backups/deletion are owner operations.
    """
    def __init__(self, path: Path, binding: str, *, create=False,
                 max_requests=MAX_REQUESTS, max_micro_usd=MAX_MICRO_USD):
        if type(max_requests) is not int or not 1 <= max_requests <= MAX_REQUESTS:
            raise Blocked("request_limit_exceeds_authorization")
        if type(max_micro_usd) is not int or not 0 <= max_micro_usd <= MAX_MICRO_USD:
            raise Blocked("spend_limit_exceeds_authorization")
        self.path = Path(path).absolute()
        if self.path.is_symlink():
            raise Blocked("ledger_symlink")
        if create:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        self.connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10, isolation_level=None)
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA journal_mode=DELETE")
        if create:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.connection.execute("CREATE TABLE policy (binding TEXT, max_requests INTEGER, max_micro_usd INTEGER)")
                self.connection.execute("INSERT INTO policy VALUES (?,?,?)", (binding, max_requests, max_micro_usd))
                self.connection.execute("""CREATE TABLE attempts (
                    invocation TEXT PRIMARY KEY, arm TEXT NOT NULL, case_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL, source_sha TEXT NOT NULL,
                    reserved_micro_usd INTEGER NOT NULL, created_ns INTEGER NOT NULL,
                    outcome TEXT NOT NULL, http_status INTEGER, latency_ms REAL,
                    input_tokens INTEGER, usage_estimate_micro_usd INTEGER,
                    response_sha256 TEXT, billed_micro_usd INTEGER,
                    UNIQUE (arm,case_id))""")
                self.connection.execute("COMMIT")
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise
        observed = self.connection.execute("SELECT * FROM policy").fetchall()
        if observed != [(binding, max_requests, max_micro_usd)]:
            self.connection.close()
            raise Blocked("ledger_authorization_binding_mismatch")
        self.binding = binding
        self.max_requests = max_requests
        self.max_micro_usd = max_micro_usd

    def reserve(self, arm: str, case_id: str, body: bytes) -> str:
        if arm not in ARMS or not isinstance(case_id, str) or not case_id:
            raise Blocked("invalid_invocation")
        invocation = sha(canonical([self.binding, arm, case_id]))
        c = self.connection
        c.execute("BEGIN IMMEDIATE")
        try:
            count, total = c.execute("SELECT count(*),coalesce(sum(reserved_micro_usd),0) FROM attempts").fetchone()
            if count >= self.max_requests or total + RESERVE_MICRO_USD > self.max_micro_usd:
                raise Blocked("authorization_budget_exhausted")
            c.execute("INSERT INTO attempts (invocation,arm,case_id,payload_sha256,source_sha,reserved_micro_usd,created_ns,outcome) VALUES (?,?,?,?,?,?,?,?)",
                      (invocation, arm, case_id, sha(body), PINS[arm], RESERVE_MICRO_USD, time.time_ns(), "reserved_unknown_dispatch"))
            c.execute("COMMIT")  # FULL-sync commit finishes before any network operation.
        except sqlite3.IntegrityError:
            c.execute("ROLLBACK")
            raise Blocked("retry_or_duplicate_invocation_forbidden") from None
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return invocation

    def finish(self, invocation: str, outcome: str, *, status=None, latency_ms=None,
               tokens=None, estimate=None, response_hash=None):
        if outcome not in ("observed_usage", "uncertain", "http_error", "invalid_response"):
            raise Blocked("invalid_accounting_outcome")
        cursor = self.connection.execute("""UPDATE attempts SET outcome=?,http_status=?,latency_ms=?,
            input_tokens=?,usage_estimate_micro_usd=?,response_sha256=?
            WHERE invocation=? AND outcome='reserved_unknown_dispatch'""",
            (outcome, status, latency_ms, tokens, estimate, response_hash, invocation))
        if cursor.rowcount != 1:
            raise Blocked("missing_or_already_resolved_invocation")

    def summary(self) -> dict:
        rows = self.connection.execute("SELECT outcome,reserved_micro_usd,usage_estimate_micro_usd,http_status FROM attempts").fetchall()
        return {"reserved_requests": len(rows), "request_limit": self.max_requests,
                "reserved_micro_usd": sum(r[1] for r in rows), "spend_limit_micro_usd": self.max_micro_usd,
                "observed_http_responses": sum(r[3] is not None for r in rows),
                "unresolved_or_failed_requests": sum(r[0] != "observed_usage" for r in rows),
                "usage_estimate_micro_usd": sum(r[2] or 0 for r in rows),
                "billed_cost_micro_usd": 0 if not rows else None,
                "cost_note": "usage estimates are not billing; all reservations retained",
                "outcomes": [r[0] for r in rows]}

    def close(self):
        self.connection.close()


class TypeSafeShadowTransport:
    """One fresh verified HTTPS connection, one POST, no replay/redirect/fallback.

    Same broker may be injected through each pinned client's transport seam.
    The key never enters payloads, plans, ledger, command arguments or logs.
    """
    def __init__(self, *, api_key: str, account_reference: str,
                 approved: ApprovedPayloads, ledger: BudgetLedger):
        if not isinstance(api_key, str) or not api_key.strip():
            raise Blocked("missing_credential")
        # Local identity/organization references must already be verified;
        # a caller cannot pass a new account while reusing an old proof/ledger.
        if (account_reference != approved.account_reference or
                sha(api_key.encode()) != approved.credential_sha256 or
                ledger.binding != approved.binding + ":" + account_reference):
            raise Blocked("transport_account_binding_mismatch")
        self.key = api_key
        self.approved = approved
        self.ledger = ledger

    def send(self, case_id: str, arm: str, payload: dict, timeout=15.0) -> dict:
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 15:
            raise Blocked("invalid_timeout")
        body = self.approved.validate(case_id, arm, payload)
        if self.key.encode() in body:
            raise Blocked("credential_in_outbound_body")
        invocation = self.ledger.reserve(arm, case_id, body)
        connection = None
        started = time.monotonic_ns()
        status = None
        outcome = "uncertain"
        tokens = estimate = response_hash = None
        try:
            connection = http.client.HTTPSConnection(HOST, timeout=timeout, context=ssl.create_default_context())
            connection.request("POST", PATH, body=body, headers={
                "Authorization": "Bearer " + self.key, "Content-Type": "application/json",
                "Accept": "application/json", "Connection": "close",
            })
            response = connection.getresponse()
            status = response.status
            if status != 200:
                outcome = "http_error"
                raise Uncertain("provider_http_failure_no_retry")
            raw = response.read(MAX_RESPONSE + 1)
            response_hash = sha(raw)
            if len(raw) > MAX_RESPONSE:
                raise ValueError("oversized_response")
            parsed = strict_loads(raw)
            if not isinstance(parsed, dict) or parsed.get("model") != MODEL or not isinstance(parsed.get("answers"), dict):
                raise ValueError("unexpected_response_shape_or_model")
            usage = parsed.get("usage")
            tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
            if type(tokens) is not int or not 0 <= tokens <= TOKEN_CEILING:
                raise ValueError("unknown_or_out_of_bound_usage")
            estimate = (tokens * RATE_MICRO_USD_PER_MILLION + 999_999) // 1_000_000
            outcome = "observed_usage"
            return parsed
        except Uncertain:
            raise
        except (ValueError, TypeError, UnicodeError):
            outcome = "invalid_response"
            raise Uncertain("invalid_provider_response_no_retry") from None
        except Exception:
            raise Uncertain("transport_uncertain_no_retry") from None
        finally:
            # If accounting itself fails, the original committed reservation remains.
            try:
                if connection is not None:
                    connection.close()
            finally:
                self.ledger.finish(invocation, outcome, status=status,
                    latency_ms=(time.monotonic_ns() - started) / 1_000_000,
                    tokens=tokens if type(tokens) is int and 0 <= tokens <= TOKEN_CEILING else None,
                    estimate=estimate, response_hash=response_hash)

    def switchyard(self, case_id: str):
        return lambda payload: self.send(case_id, "switchyard", payload)

    def jev_skills(self, case_id: str, *, error_type):
        def transport(body, _headers, timeout):
            # Discard caller headers; only the fixed broker-owned header set is sent.
            try:
                payload = strict_loads(body)
                return canonical(self.send(case_id, "jev-skills", payload, timeout=min(timeout, 15.0)))
            except (Blocked, Uncertain, ValueError, TypeError):
                # The pinned route catches its own JevError, not arbitrary broker
                # errors. This code is outside the SDK's retryable error set.
                raise error_type("shadow_no_retry", "shadow broker blocked or uncertain") from None
        return transport
