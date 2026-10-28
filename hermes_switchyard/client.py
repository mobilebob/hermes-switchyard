"""Fixed-route OpenRouter Decisions API client for Jev."""
from __future__ import annotations

import email.utils
import http.client
import inspect
import json
import logging
import math
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
from collections.abc import Callable
from typing import Any, cast

from . import receipt_state

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
TYPESAFE_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
OPENROUTER_APP_HEADERS = {
    "HTTP-Referer": "https://github.com/bgrablin/hermes-switchyard",
    "X-Title": "Hermes-Switchyard",
    "X-OpenRouter-Title": "Hermes-Switchyard",
    "X-OpenRouter-Categories": "personal-agent,cli-agent",
}
OPENROUTER_MODELS = frozenset({"typesafe/jev-1.13", "typesafe/jev-1.13-20260917"})
TYPESAFE_MODELS = frozenset({"jev-latest", "jev-1.13", "jev-1.13.0", "jev-1.14", "jev-1.14.0"})
ALLOWED_ENDPOINTS = frozenset({DEFAULT_ENDPOINT, TYPESAFE_ENDPOINT})
ALLOWED_MODELS = OPENROUTER_MODELS | TYPESAFE_MODELS
EXPECTED_MODEL = "typesafe/jev-1.13"
MAX_QUESTIONS_PER_REQUEST = 255
MAX_REQUEST_BYTES = 96_000
MAX_RESPONSE_BYTES = 1_048_576
MAX_ERROR_BYTES = 16_384
MAX_DECISION_REQUESTS = 64
MAX_TOTAL_QUESTIONS = MAX_QUESTIONS_PER_REQUEST * MAX_DECISION_REQUESTS
MAX_OPERATION_REQUESTS = 256
DEFAULT_OPERATION_DEADLINE_SECONDS = 60.0
# Automatic pre_llm_call advice must finish before a typical Hermes plugin
# callback budget (~30s). Keep this comfortably below that host timeout and
# separate from explicit decision / computer-use deadlines (60s).
DEFAULT_AUTOMATIC_ROUTING_DEADLINE_SECONDS = 20.0
# Conservatively replace a pooled connection after 60 s idle to avoid reusing
# a socket that may have closed server-side. No live idle-close threshold has
# been measured for this route (issue #92).
MAX_CONNECTION_IDLE_SECONDS = 60.0
# Errors that mean the server closed a reused keep-alive connection before it
# sent any response. RemoteDisconnected is a ConnectionResetError; it is listed
# for clarity. Timeouts are not in this set and are never replayed.
_STALE_CONNECTION_ERRORS = (
    http.client.RemoteDisconnected,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
    ssl.SSLEOFError,
)
# HTTP 429 (rate limited) and 529 (overloaded) mean the provider did not run
# the request. A decision request is a pure function of its inputs, so a
# bounded replay is safe. Each retry waits for the larger of the exponential
# backoff and a numeric or HTTP-date ``Retry-After`` value. A retry is not
# started when the wait exceeds MAX_RETRY_AFTER_SECONDS or would leave less
# than MIN_RETRY_REMAINING_SECONDS of the operation deadline.
RETRYABLE_HTTP_STATUSES = frozenset({429, 529})
MAX_RATE_LIMIT_RETRIES = 2
RATE_LIMIT_BACKOFF_BASE_SECONDS = 0.5
MAX_RETRY_AFTER_SECONDS = 8.0
MIN_RETRY_REMAINING_SECONDS = 1.0
_RETRY_SLEEP_SLICE_SECONDS = 0.25
# Transport retries (stale connection plus 429/529) across one bounded
# operation scope. Each logical request also has its own per-request bound.
MAX_OPERATION_RETRIES = 4
# Closed set of safe diagnostic sub-codes for a hosted request failure. No
# provider text, header value, URL, or payload data is ever part of a code.
HOSTED_ERROR_DETAILS = receipt_state.HOSTED_ERROR_DETAILS
# Closed set of retry reasons recorded in call metadata.
TRANSPORT_RETRY_REASONS = frozenset({"stale_connection", "http_429", "http_529"})
# A default log shows at most one WARNING per failure sub-code per interval.
HOSTED_FAILURE_WARNING_INTERVAL_SECONDS = 300.0
_WARNING_LOCK = threading.Lock()
_WARNING_LAST: dict[str, float] = {}
_WARNING_SUPPRESSED: dict[str, int] = {}

_RESPONSE_FIELDS = frozenset({"model", "answers", "usage", "latency_ms", "request_id"})
_OPERATION_DEADLINE: ContextVar[float | None] = ContextVar(
    "jev_operation_deadline", default=None
)
_HOST_CANCEL_CHECK: ContextVar[Any] = ContextVar(
    "jev_host_cancel_check", default=None
)
_DEFER_HOSTED_WARNING: ContextVar[bool] = ContextVar(
    "jev_defer_hosted_warning", default=False
)


class DeadlineExceeded(TimeoutError):
    """Aggregate operation wall-clock deadline expired before or between requests."""


class LateResultDiscarded(TimeoutError):
    """Provider work finished after the consumer deadline; the result must not be used."""


class HostCancelled(TimeoutError):
    """Host abandoned the plugin callback; stop further requests."""


class JevRequestError(RuntimeError):
    """A hosted request failure with a closed-set diagnostic sub-code.

    The message keeps the stable local wording used before. ``detail`` is one
    member of HOSTED_ERROR_DETAILS and never carries provider text.
    """

    def __init__(self, message: str, *, detail: str) -> None:
        super().__init__(message)
        self.detail = detail if detail in HOSTED_ERROR_DETAILS else "unknown"


def _http_status_detail(status: int) -> str:
    if status in (401, 403, 404, 429, 529):
        return f"http_{status}"
    if 300 <= status < 400:
        return "redirect"
    if 400 <= status < 500:
        return "http_4xx"
    return "http_5xx"


def hosted_error_detail(exc: BaseException | None) -> str:
    """Map a hosted failure to one closed-set diagnostic sub-code.

    This is a pure function of the exception type and of the typed ``detail``
    set by this module. It never reads provider text.
    """
    seen = 0
    while exc is not None and seen < 8:
        seen += 1
        if isinstance(exc, PartialAccountingError) and exc.__cause__ is not None:
            exc = exc.__cause__
            continue
        if isinstance(exc, JevRequestError):
            return exc.detail
        if isinstance(exc, HostCancelled):
            return "host_cancelled"
        if isinstance(exc, LateResultDiscarded):
            return "late_result_discarded"
        if isinstance(exc, DeadlineExceeded):
            return "deadline_exceeded"
        if isinstance(exc, PermissionError):
            return "ack_required"
        if isinstance(exc, TimeoutError):
            return "timeout"
        if isinstance(exc, ValueError):
            if "budget exceeded" in str(exc):
                return "request_budget_exhausted"
            return "validation_failure"
        if isinstance(exc, TypeError):
            return "invalid_response"
        if isinstance(exc, (ConnectionError, ssl.SSLError)):
            return "connect_failed"
        return "unknown"
    return "unknown"


def _warn_hosted_failure(detail: str) -> None:
    """Log one WARNING per sub-code per interval; count the suppressed ones."""
    if detail == "host_cancelled":
        return
    now = time.monotonic()
    with _WARNING_LOCK:
        last = _WARNING_LAST.get(detail)
        if last is not None and now - last < HOSTED_FAILURE_WARNING_INTERVAL_SECONDS:
            _WARNING_SUPPRESSED[detail] = _WARNING_SUPPRESSED.get(detail, 0) + 1
            return
        suppressed = _WARNING_SUPPRESSED.pop(detail, 0)
        _WARNING_LAST[detail] = now
    logger.warning(
        "Jev hosted request failed (%s); %d similar failures suppressed since the last warning.",
        detail,
        suppressed,
    )


def _reset_warning_rate_limit() -> None:
    """Clear warning rate-limit state (tests only)."""
    with _WARNING_LOCK:
        _WARNING_LAST.clear()
        _WARNING_SUPPRESSED.clear()


@contextmanager
def _defer_hosted_warning():
    """Let an enclosing automatic turn warn once, including pre-decision failures."""
    token = _DEFER_HOSTED_WARNING.set(True)
    try:
        yield
    finally:
        _DEFER_HOSTED_WARNING.reset(token)


def parse_retry_after(value: Any, *, now: float | None = None) -> float | None:
    """Return a non-negative Retry-After delay in seconds, or None if invalid.

    Accepts delta-seconds or an HTTP-date. The value is parsed locally and is
    never logged or stored.
    """
    if type(value) is not str:
        return None
    text = value.strip()
    if not text or len(text) > 64:
        return None
    if text.isascii() and text.isdigit():
        return float(int(text))
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when is None or when.tzinfo is None:
        return None
    current = time.time() if now is None else now
    return max(0.0, when.timestamp() - current)


def merge_transport_retries(total: dict[str, int], retries: Any) -> None:
    """Add closed-set retry counts from ``retries`` into ``total``."""
    if not isinstance(retries, dict):
        return
    for reason, count in retries.items():
        if reason in TRANSPORT_RETRY_REASONS and type(count) is int and 0 < count <= MAX_OPERATION_RETRIES * 1024:
            total[reason] = total.get(reason, 0) + count


def _validate_deadline_seconds(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("deadline_seconds must be a finite positive number")
    return float(value)


@contextmanager
def operation_deadline_scope(deadline_seconds: float | None):
    """Install one monotonic deadline for every bounded operation layer."""
    if deadline_seconds is None:
        yield
        return
    duration = _validate_deadline_seconds(deadline_seconds)
    current = _OPERATION_DEADLINE.get()
    candidate = time.monotonic() + duration
    effective = candidate if current is None else min(current, candidate)
    token = _OPERATION_DEADLINE.set(effective)
    try:
        yield
    finally:
        _OPERATION_DEADLINE.reset(token)


@contextmanager
def host_cancel_scope(cancel_check: Callable[[], bool] | None):
    """Install an optional host-cancel predicate for cooperative abort."""
    if cancel_check is None:
        yield
        return
    token = _HOST_CANCEL_CHECK.set(cancel_check)
    try:
        yield
    finally:
        _HOST_CANCEL_CHECK.reset(token)


def operation_remaining_deadline() -> float | None:
    """Return remaining aggregate time, or raise once the operation expires.

    Also raises ``HostCancelled`` when an installed host-cancel check reports
    that the consumer abandoned the callback.
    """
    cancel_check = _HOST_CANCEL_CHECK.get()
    if callable(cancel_check):
        try:
            cancelled = bool(cancel_check())
        except Exception:  # noqa: BLE001 -- cancel probes must not mask deadlines
            cancelled = False
        if cancelled:
            raise HostCancelled("host cancelled the plugin callback")
    deadline = _OPERATION_DEADLINE.get()
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DeadlineExceeded("Jev aggregate deadline exceeded")
    return remaining


def _request_budget_context(
    factory: Callable[..., Any], max_requests: int, deadline_seconds: float | None
):
    if deadline_seconds is None:
        return factory(max_requests)
    try:
        parameters = inspect.signature(factory).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    supports_deadline = any(
        parameter.name == "deadline_seconds"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
    if supports_deadline:
        return factory(max_requests, deadline_seconds=deadline_seconds)
    return factory(max_requests)


def request_budget_scope(
    client: Any,
    max_requests: int = MAX_DECISION_REQUESTS,
    *,
    deadline_seconds: float | None = None,
) -> AbstractContextManager[None]:
    @contextmanager
    def scoped_operation():
        with operation_deadline_scope(deadline_seconds):
            factory = getattr(client, "request_budget", None)
            if not callable(factory):
                with nullcontext():
                    yield
                return
            scoped = _request_budget_context(factory, max_requests, deadline_seconds)
            with cast(AbstractContextManager[None], scoped):
                yield

    return scoped_operation()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r} is not allowed")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _strict_json_loads(raw: bytes) -> Any:
    return json.loads(
        raw,
        object_pairs_hook=_reject_duplicate_pairs,
        parse_constant=_reject_json_constant,
    )


def _parse_error_body(raw: bytes) -> None:
    """Validate JSON error envelopes without retaining provider-controlled text."""
    if not raw or not raw.strip():
        return
    try:
        parsed = _strict_json_loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        # Plain-text HTTP errors are tolerated, but JSON-looking error bodies
        # must satisfy the same duplicate/non-finite boundary as success data.
        if raw.lstrip().startswith((b"{", b"[")):
            raise RuntimeError("Jev provider returned invalid error JSON") from None
        return
    if not isinstance(parsed, dict):
        raise RuntimeError("Jev provider returned an invalid error envelope")
    _reject_nonfinite_values(parsed)


def _reject_nonfinite_values(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number is not allowed")
    if isinstance(value, dict):
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("JSON object keys must be strings")
            _reject_nonfinite_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_nonfinite_values(item)


def _strip_response_controls(result: dict[str, Any]) -> dict[str, Any]:
    """Keep only the typed response envelope; provider controls stay local."""
    _reject_nonfinite_values(result)
    # OpenRouter's documented Decisions response carries its request identifier
    # as `id`; older fixtures use `request_id`. Normalize to the canonical
    # internal field before the allowlist drops unknown wire fields. Exactly one
    # identifier may be present; contradictory dual identifiers are rejected
    # rather than resolved silently.
    wire_id = result.get("id")
    canonical_id = result.get("request_id")
    if wire_id is not None:
        if type(wire_id) is not str or not 0 < len(wire_id) <= 128 or not wire_id.isprintable():
            raise ValueError("Jev response identifier is not a bounded printable string")
        if canonical_id is not None and canonical_id != wire_id:
            raise ValueError("Jev response carries contradictory identifiers")
        canonical_id = wire_id
    stripped = {key: result[key] for key in _RESPONSE_FIELDS if key in result}
    if canonical_id is not None:
        stripped["request_id"] = canonical_id
    return stripped


def _require_public_data_ack(acknowledged: bool = True) -> None:
    if acknowledged is not True:
        raise PermissionError(
            "public_or_sanitized_data_ack is false; this call was refused"
        )


def _valid_model_slug(model: Any, endpoint: str | None = None) -> bool:
    if type(model) is not str:
        return False
    if endpoint == DEFAULT_ENDPOINT:
        return model in OPENROUTER_MODELS
    if endpoint == TYPESAFE_ENDPOINT:
        return model in TYPESAFE_MODELS
    return model in ALLOWED_MODELS


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject all redirects so a bearer token cannot be forwarded elsewhere."""

    @staticmethod
    def _reject(request, _fp, code, _message, headers):
        raise urllib.error.HTTPError(request.full_url, code, "redirects disabled", headers, None)

    http_error_301 = _reject
    http_error_302 = _reject
    http_error_303 = _reject
    http_error_307 = _reject
    http_error_308 = _reject


class PartialAccountingError(RuntimeError):
    """Raised when a later request fails after earlier responses were recorded.

    Carries the bounded ledger of responses observed before the failure so the
    caller can surface honest accounting (request IDs, usage, timing) at the
    terminal boundary instead of discarding earlier successful attempts.
    """

    def __init__(self, message: str, *, partial: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.partial = list(partial)


class DecisionClient:
    """Call one of the fixed Jev endpoints and require exact model resolution."""

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = EXPECTED_MODEL,
        timeout: float = 25,
        transport: Callable[[dict], dict] | None = None,
        max_connection_idle_seconds: float = MAX_CONNECTION_IDLE_SECONDS,
    ):
        if type(api_key) is not str or not api_key.strip():
            raise ValueError("Jev API key is required")
        if endpoint not in ALLOWED_ENDPOINTS:
            raise ValueError("Jev endpoint is not an evidence-backed fixed endpoint")
        if not _valid_model_slug(model, endpoint):
            raise ValueError("Jev model is not an evidence-backed allowed alias for this endpoint")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a finite positive number")
        if (
            type(max_connection_idle_seconds) not in (int, float)
            or not math.isfinite(max_connection_idle_seconds)
            or not 0 < max_connection_idle_seconds <= 3600
        ):
            raise ValueError("max_connection_idle_seconds must be between 0 and 3600")
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.timeout = float(timeout)
        self.max_connection_idle_seconds = float(max_connection_idle_seconds)
        self.transport = transport
        self._url = urllib.parse.urlsplit(endpoint)
        self._connection: http.client.HTTPSConnection | None = None
        self._connection_last_used: float | None = None
        # Count of requests replayed after a pooled connection was found closed.
        self.stale_connection_retries = 0
        # Count of requests replayed after HTTP 429 or 529.
        self.rate_limit_retries = 0
        self._connection_lock = threading.RLock()
        self._request_budget: ContextVar[int | None] = ContextVar(
            f"jev_request_budget_{id(self)}", default=None
        )
        # Transport retries left in the current bounded operation scope.
        self._retry_budget: ContextVar[list[int] | None] = ContextVar(
            f"jev_retry_budget_{id(self)}", default=None
        )
        # Closed-set retry counts for the logical request in progress.
        self._call_retries: ContextVar[dict[str, int] | None] = ContextVar(
            f"jev_call_retries_{id(self)}", default=None
        )
        self._operation_deadline: ContextVar[float | None] = ContextVar(
            f"jev_operation_deadline_{id(self)}", default=None
        )

    @contextmanager
    def request_budget(
        self,
        max_requests: int = MAX_DECISION_REQUESTS,
        *,
        deadline_seconds: float | None = None,
    ):
        if type(max_requests) is not int or not 1 <= max_requests <= MAX_OPERATION_REQUESTS:
            raise ValueError("max_requests is outside the bounded operation budget")
        if deadline_seconds is not None and (
            type(deadline_seconds) not in (int, float)
            or not math.isfinite(deadline_seconds)
            or deadline_seconds <= 0
        ):
            raise ValueError("deadline_seconds must be a finite positive number")
        current = self._request_budget.get()
        if current is not None:
            yield
            return
        token = self._request_budget.set(max_requests)
        retry_token = self._retry_budget.set([MAX_OPERATION_RETRIES])
        deadline_token = self._operation_deadline.set(
            time.monotonic() + float(deadline_seconds)
            if deadline_seconds is not None
            else None
        )
        try:
            yield
        finally:
            self._request_budget.reset(token)
            self._retry_budget.reset(retry_token)
            self._operation_deadline.reset(deadline_token)

    def _remaining_deadline(self) -> float | None:
        remaining = operation_remaining_deadline()
        deadline = self._operation_deadline.get()
        if deadline is not None:
            local_remaining = deadline - time.monotonic()
            if local_remaining <= 0:
                raise DeadlineExceeded("Jev aggregate deadline exceeded")
            remaining = local_remaining if remaining is None else min(remaining, local_remaining)
        return remaining

    def _close_connection(self) -> None:
        self._connection_last_used = None
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None

    def _connection_idle_too_long(self) -> bool:
        last_used = self._connection_last_used
        return last_used is not None and time.monotonic() - last_used > self.max_connection_idle_seconds

    def _take_retry(self, reason: str) -> bool:
        """Reserve one transport retry from the operation budget and record it."""
        budget = self._retry_budget.get()
        if budget is not None:
            if budget[0] <= 0:
                return False
            budget[0] -= 1
        call_retries = self._call_retries.get()
        if call_retries is not None:
            call_retries[reason] = call_retries.get(reason, 0) + 1
        return True

    def _send_request(
        self,
        path: str,
        body: bytes,
        headers: dict[str, str],
        *,
        allow_stale_retry: bool = True,
    ) -> tuple[Any, bool]:
        """Send one POST; return the response and whether a stale retry ran.

        Call with the connection lock held. A pooled keep-alive connection
        that the server closed while idle fails before any response byte
        arrives. Only that case is retried, once, on a new connection. A
        decision request changes no provider state, so the replay is safe. A
        failure on a new connection, a timeout, and any failure after the
        response started are raised unchanged.
        """
        stale_retried = False
        for attempt in range(2):
            remaining = self._remaining_deadline()
            timeout = self.timeout if remaining is None else min(self.timeout, remaining)
            if self._connection is not None and self._connection_idle_too_long():
                self._close_connection()
            reused = self._connection is not None
            if self._connection is None:
                host = self._url.hostname
                if not host:
                    raise JevRequestError("Jev endpoint has no host", detail="transport_failed")
                self._connection = http.client.HTTPSConnection(
                    host,
                    self._url.port,
                    timeout=timeout,
                )
            elif getattr(self._connection, "sock", None) is not None:
                self._connection.sock.settimeout(timeout)
            try:
                self._connection.request("POST", path, body=body, headers=headers)
                return self._connection.getresponse(), stale_retried
            except _STALE_CONNECTION_ERRORS as exc:
                self._close_connection()
                if stale_retried:
                    raise JevRequestError(
                        f"Jev connection failed: {type(exc).__name__}", detail="stale_connection"
                    ) from None
                if not reused or not allow_stale_retry or attempt or not self._take_retry("stale_connection"):
                    raise JevRequestError(
                        f"Jev connection failed: {type(exc).__name__}", detail="connect_failed"
                    ) from None
                stale_retried = True
                self.stale_connection_retries += 1
                logger.info(
                    "Jev pooled connection was closed by the server (%s); retrying once on a new connection.",
                    type(exc).__name__,
                )
        raise JevRequestError("Jev connection retry did not complete", detail="stale_connection")

    @staticmethod
    def _read_bounded(response: Any, limit: int) -> bytes:
        headers = getattr(response, "headers", None)
        content_length = None
        if headers is not None:
            try:
                content_length = headers.get("Content-Length")
            except AttributeError:
                content_length = None
        if content_length is not None:
            try:
                declared = int(content_length)
            except (TypeError, ValueError):
                raise RuntimeError("Jev provider returned an invalid response length") from None
            if declared < 0 or declared > limit:
                raise RuntimeError("Jev provider response exceeded the bounded body limit")
        try:
            raw = response.read(limit + 1)
        except TypeError:
            # Test transports and old HTTPResponse shims may only expose read().
            raw = response.read()
        if not isinstance(raw, (bytes, bytearray)):
            raise RuntimeError("Jev provider returned an invalid response body")
        if len(raw) > limit:
            raise RuntimeError("Jev provider response exceeded the bounded body limit")
        return bytes(raw)

    def _retry_delay(self, status: int, retry_after: Any, retries_done: int) -> float | None:
        """Return a bounded wait before a 429/529 retry, or None to stop."""
        if retries_done >= MAX_RATE_LIMIT_RETRIES:
            return None
        delay = RATE_LIMIT_BACKOFF_BASE_SECONDS * (2 ** retries_done)
        parsed = parse_retry_after(retry_after)
        if parsed is not None:
            if parsed > MAX_RETRY_AFTER_SECONDS:
                return None
            delay = max(delay, parsed)
        delay = min(delay, MAX_RETRY_AFTER_SECONDS)
        remaining = self._remaining_deadline()
        if remaining is not None and delay + MIN_RETRY_REMAINING_SECONDS > remaining:
            return None
        return delay

    def _sleep_before_retry(self, delay: float) -> None:
        """Sleep in short slices so a deadline or host cancel stops the wait."""
        end = time.monotonic() + delay
        while True:
            self._remaining_deadline()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(_RETRY_SLEEP_SLICE_SECONDS, left))

    def _post_attempt(
        self, path: str, body: bytes, headers: dict[str, str], *, allow_stale_retry: bool
    ) -> tuple[bytes | None, int | None, Any, bool]:
        """Run one HTTP exchange under the connection lock.

        Returns ``(raw, None, None, stale_retried)`` on success, or
        ``(None, status, retry_after, stale_retried)`` for a retryable 429/529.
        """
        with self._connection_lock:
            stale_retried = False
            try:
                response, stale_retried = self._send_request(
                    path, body, headers, allow_stale_retry=allow_stale_retry
                )
                status = response.status
                if 300 <= status < 400:
                    _parse_error_body(self._read_bounded(response, MAX_ERROR_BYTES))
                    raise JevRequestError(
                        "Jev provider returned an unexpected redirect", detail="redirect"
                    )
                if status >= 400:
                    if status in RETRYABLE_HTTP_STATUSES:
                        retry_after = None
                        response_headers = getattr(response, "headers", None)
                        if response_headers is not None:
                            try:
                                retry_after = response_headers.get("Retry-After")
                            except AttributeError:
                                retry_after = None
                        # Status and headers suffice. Do not parse an untrusted
                        # error body before a bounded 429/529 retry.
                        try:
                            close_response = getattr(response, "close", None)
                            if callable(close_response):
                                close_response()
                        finally:
                            self._close_connection()
                        return None, status, retry_after, stale_retried
                    _parse_error_body(self._read_bounded(response, MAX_ERROR_BYTES))
                    raise JevRequestError(
                        f"Jev provider returned HTTP {status}",
                        detail=_http_status_detail(status),
                    )
                raw = self._read_bounded(response, MAX_RESPONSE_BYTES)
                self._connection_last_used = time.monotonic()
                if getattr(response, "will_close", False):
                    self._close_connection()
                close_response = getattr(response, "close", None)
                if callable(close_response):
                    close_response()
                return raw, None, None, stale_retried
            except JevRequestError:
                self._close_connection()
                raise
            except RuntimeError as exc:
                self._close_connection()
                raise JevRequestError(str(exc), detail="invalid_response") from None
            except TimeoutError:
                self._close_connection()
                raise
            except (OSError, http.client.HTTPException) as exc:
                self._close_connection()
                detail = "invalid_response" if isinstance(exc, http.client.HTTPException) else "connect_failed"
                raise JevRequestError(
                    f"Jev connection failed: {type(exc).__name__}", detail=detail
                ) from None

    def _post(self, payload: dict) -> dict:
        self._remaining_deadline()
        if self.transport is not None:
            try:
                result = self.transport(payload)
            except (DeadlineExceeded, LateResultDiscarded, HostCancelled):
                raise
            except TimeoutError:
                raise
            except Exception as exc:  # noqa: BLE001 - never expose transport/payload details
                raise JevRequestError(
                    f"Jev transport failed: {type(exc).__name__}", detail="transport_failed"
                ) from None
            try:
                self._remaining_deadline()
            except DeadlineExceeded as exc:
                # The consumer can no longer use this response. Discard it and
                # record a distinct late-result outcome rather than publishing.
                raise LateResultDiscarded(
                    "Jev late result discarded after aggregate deadline"
                ) from exc
            if not isinstance(result, dict):
                raise TypeError("Jev transport returned a non-object response")
            return _strip_response_controls(result)
        try:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            raise ValueError("Jev request contains a non-JSON value") from None
        path = self._url.path or "/"
        if self._url.query:
            path += f"?{self._url.query}"
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
            "Connection": "keep-alive",
        }
        if self.endpoint == DEFAULT_ENDPOINT:
            headers.update(OPENROUTER_APP_HEADERS)
        allow_stale_retry = True
        rate_retries = 0
        while True:
            raw, status, retry_after, stale_retried = self._post_attempt(
                path, body, headers, allow_stale_retry=allow_stale_retry
            )
            if stale_retried:
                # At most one stale-connection replay per logical request.
                allow_stale_retry = False
            if raw is not None:
                break
            assert status is not None
            detail = _http_status_detail(status)
            delay = self._retry_delay(status, retry_after, rate_retries)
            if delay is None:
                raise JevRequestError(f"Jev provider returned HTTP {status}", detail=detail)
            if not self._take_retry(detail):
                raise JevRequestError(
                    f"Jev provider returned HTTP {status}", detail="retry_budget_exhausted"
                )
            rate_retries += 1
            self.rate_limit_retries += 1
            logger.info(
                "Jev provider returned HTTP %d; retry %d of %d after %.2f s.",
                status,
                rate_retries,
                MAX_RATE_LIMIT_RETRIES,
                delay,
            )
            self._sleep_before_retry(delay)
        try:
            self._remaining_deadline()
        except DeadlineExceeded as exc:
            raise LateResultDiscarded(
                "Jev late result discarded after aggregate deadline"
            ) from exc
        try:
            result = _strict_json_loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise JevRequestError(
                "Jev provider returned invalid JSON", detail="invalid_response"
            ) from None
        if not isinstance(result, dict):
            raise TypeError("Jev provider returned a non-object response")
        return _strip_response_controls(result)

    def _validate_resolved_model(self, resolved: Any) -> str:
        if not _valid_model_slug(resolved, self.endpoint):
            raise ValueError("Jev provider resolved a model outside the allowed aliases")
        # Aliases may resolve to a dated/concrete release, but an explicitly
        # pinned model must resolve exactly to that model.
        if self.endpoint == DEFAULT_ENDPOINT and self.model == EXPECTED_MODEL and resolved in OPENROUTER_MODELS:
            return resolved
        if self.endpoint == TYPESAFE_ENDPOINT and self.model == "jev-latest" and resolved in TYPESAFE_MODELS:
            return resolved
        if resolved != self.model:
            raise ValueError("Jev provider resolved a different allowed alias")
        return resolved

    @staticmethod
    def _validate_usage(usage: Any) -> dict[str, Any]:
        if not isinstance(usage, dict):
            raise TypeError("Jev response usage must be an object")
        if "cost" in usage:
            cost = usage["cost"]
            if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
                raise ValueError("Invalid Jev usage cost")
        return receipt_state.safe_usage(usage)

    @staticmethod
    def _validate_questions(questions: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(questions, dict) or not questions:
            raise ValueError("questions must be a non-empty object")
        if len(questions) > MAX_TOTAL_QUESTIONS:
            raise ValueError("question count exceeds the bounded provider-request budget")
        validated: dict[str, dict[str, Any]] = {}
        for name, question in questions.items():
            if type(name) is not str or not name or name != name.strip():
                raise ValueError("question names must be non-empty exact strings")
            if not isinstance(question, dict) or set(question) - {"type", "instructions", "criteria"}:
                raise ValueError(f"Invalid question {name}")
            question_type = question.get("type")
            instructions = question.get("instructions")
            criteria = question.get("criteria")
            if type(instructions) is not str or not instructions.strip():
                raise ValueError(f"Invalid question instructions {name}")
            if question_type == "choice":
                if not isinstance(criteria, dict) or not 1 <= len(criteria) <= 255:
                    raise ValueError(f"Invalid choice criteria {name}")
                if any(type(key) is not str or not key or type(value) is not str for key, value in criteria.items()):
                    raise ValueError(f"Invalid choice criteria {name}")
            elif question_type == "score":
                if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10 or any(type(item) is not str for item in criteria):
                    raise ValueError(f"Invalid score criteria {name}")
            elif question_type == "noul":
                if criteria is not None and (
                    not isinstance(criteria, dict)
                    or any(type(key) is not str or type(value) is not str for key, value in criteria.items())
                ):
                    raise ValueError(f"Invalid noul criteria {name}")
            else:
                raise ValueError(f"Unsupported Jev question type {question_type!r}")
            validated[name] = dict(question)
        return validated

    @staticmethod
    def _merge_usage(total: dict[str, Any], usage: dict[str, Any]) -> None:
        receipt_state.merge_usage(total, usage)

    def _payload(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        payload = {"model": self.model, "state": state, "questions": questions}
        if self.endpoint == DEFAULT_ENDPOINT:
            payload["provider"] = {"allow_fallbacks": False}
        if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_REQUEST_BYTES:
            raise ValueError("Jev request exceeds the bounded serialized request budget")
        return payload

    def _decide_single(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict:
        started = time.perf_counter()
        call_retries: dict[str, int] = {}
        retries_token = self._call_retries.set(call_retries)
        try:
            result = self._post(self._payload(state, questions))
        finally:
            self._call_retries.reset(retries_token)
        if not isinstance(result, dict):
            raise TypeError("Jev response must be an object")
        result["model"] = self._validate_resolved_model(result.get("model"))
        answers = result.get("answers")
        if not isinstance(answers, dict):
            raise TypeError("Jev response has no answers object")
        if any(type(key) is not str for key in answers) or set(answers) != set(questions):
            raise ValueError("Jev response answer keys do not exactly match the requested questions")
        validated_answers: dict[str, dict[str, Any]] = {}
        for name, question in questions.items():
            answer = answers.get(name)
            if not isinstance(answer, dict):
                raise TypeError(f"Jev response is missing answer {name}")
            question_type = question["type"]
            answer_type = answer.get("type")
            if answer_type is not None:
                if answer_type != question_type:
                    raise ValueError(f"Jev answer {name} type does not match the requested question")
                answer = {key: value for key, value in answer.items() if key != "type"}
            if question_type == "choice":
                if set(answer) != {"choice", "probabilities", "confidence"}:
                    raise ValueError(f"Jev choice {name} has unexpected response fields")
                self._validate_choice(name, answer, question["criteria"])
                validated_answers[name] = {
                    key: answer[key] for key in ("choice", "probabilities", "confidence")
                }
            elif question_type == "score":
                if set(answer) != {"score", "legend", "probabilities", "confidence"}:
                    raise ValueError(f"Jev score {name} has unexpected response fields")
                self._validate_score(name, answer, question["criteria"])
                validated_answers[name] = {
                    key: answer[key]
                    for key in ("score", "legend", "probabilities", "confidence")
                }
            else:
                if set(answer) != {"noul"}:
                    raise ValueError(f"Jev noul {name} has unexpected response fields")
                value = answer.get("noul")
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError(f"Invalid noul answer {name}")
                validated_answers[name] = {"noul": value}
        result["answers"] = validated_answers
        result["usage"] = self._validate_usage(result.get("usage", {}))
        latency = result.get("latency_ms")
        if latency is None:
            latency = round((time.perf_counter() - started) * 1000, 1)
        elif type(latency) not in (int, float) or not math.isfinite(latency) or latency < 0:
            raise ValueError("Invalid Jev latency")
        result["latency_ms"] = float(latency)
        if call_retries:
            result["transport_retries"] = dict(call_retries)
        return result

    def _question_batches(self, state: Any, validated: dict[str, dict[str, Any]]) -> list[dict[str, dict[str, Any]]]:
        """Preserve exact batch boundaries while sizing growing payloads once."""
        if len(validated) <= MAX_QUESTIONS_PER_REQUEST:
            try:
                self._payload(state, validated)
            except ValueError:
                pass  # A valid multi-question request may need byte-sized batches.
            else:
                return [validated]
        batches: list[dict[str, dict[str, Any]]] = []
        current: dict[str, dict[str, Any]] = {}
        # The state is constant across batches. JSON's default ", " separator
        # lets us size each additional question exactly without reserializing
        # the entire state and every preceding question. _decide_single still
        # validates the complete payload immediately before transport.
        base_size = len(json.dumps(self._payload(state, {}), ensure_ascii=False, allow_nan=False).encode("utf-8"))
        current_size = base_size
        for name, question in validated.items():
            entry_size = len(json.dumps({name: question}, ensure_ascii=False, allow_nan=False).encode("utf-8")) - 2
            trial_size = current_size + entry_size + (2 if current else 0)
            if current and (trial_size > MAX_REQUEST_BYTES or len(current) >= MAX_QUESTIONS_PER_REQUEST):
                batches.append(current)
                current = {}
                trial_size = base_size + entry_size
            if trial_size > MAX_REQUEST_BYTES:
                raise ValueError("Jev request exceeds the bounded serialized request budget")
            current[name] = question
            current_size = trial_size
        if current:
            batches.append(current)
        return batches

    def decide(
        self,
        state: Any,
        questions: dict,
        *,
        public_or_sanitized_data_ack: bool = True,
    ) -> dict:
        """Return a validated typed response. Omit ack to use the standing default (on)."""
        _require_public_data_ack(public_or_sanitized_data_ack)
        validated = self._validate_questions(questions)
        batches = self._question_batches(state, validated)
        remaining = self._request_budget.get()
        allowed = MAX_DECISION_REQUESTS if remaining is None else remaining
        if len(batches) > allowed:
            raise ValueError("Jev provider-request budget exceeded")
        if remaining is not None:
            self._request_budget.set(remaining - len(batches))
        # Record each validated response as it succeeds so a later failed
        # request cannot discard earlier accounting. When a later batch fails,
        # surface the earlier observed accounting through a typed exception at
        # the terminal boundary; when the very first batch fails, preserve the
        # original exception exactly as before.
        partial: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []
        for batch in batches:
            try:
                call = self._decide_single(state, batch)
            except Exception as exc:
                if not _DEFER_HOSTED_WARNING.get():
                    _warn_hosted_failure(hosted_error_detail(exc))
                if partial:
                    raise PartialAccountingError(
                        "Jev provider request failed after "
                        f"{len(calls)} successful batch(es)",
                        partial=partial,
                    ) from exc
                raise
            calls.append(call)
            partial.append(
                {
                    "latency_ms": call.get("latency_ms"),
                    "model": call.get("model"),
                    "request_id": call.get("request_id"),
                    "request_count": int(call.get("request_count") or 1),
                    "total_latency_ms": call.get("total_latency_ms", call.get("latency_ms")),
                    "total_usage": call.get("total_usage", call.get("usage") or {}),
                    "usage": call.get("usage") or {},
                    **(
                        {"transport_retries": dict(call["transport_retries"])}
                        if call.get("transport_retries")
                        else {}
                    ),
                }
            )
        if len(calls) == 1:
            calls[0]["request_count"] = 1
            calls[0]["total_latency_ms"] = calls[0]["latency_ms"]
            calls[0]["total_usage"] = dict(calls[0]["usage"])
            return calls[0]
        answers: dict[str, Any] = {}
        usage: dict[str, Any] = {}
        latency = 0.0
        retries: dict[str, int] = {}
        for call in calls:
            answers.update(call["answers"])
            self._merge_usage(usage, call["usage"])
            latency += call["latency_ms"]
            merge_transport_retries(retries, call.get("transport_retries"))
        combined = {
            "model": calls[-1]["model"],
            "answers": answers,
            "usage": usage,
            "latency_ms": latency,
            "request_count": len(calls),
            "total_latency_ms": latency,
            "total_usage": usage,
        }
        if retries:
            combined["transport_retries"] = retries
        return combined

    @staticmethod
    def _validate_choice(name: str, answer: dict, criteria: dict) -> None:
        if not isinstance(criteria, dict) or not criteria:
            raise ValueError(f"Jev choice {name} has no offered criteria")
        choice = answer.get("choice")
        probabilities = answer.get("probabilities")
        confidence = answer.get("confidence")
        numbers = [] if not isinstance(probabilities, dict) else [*probabilities.values(), confidence]
        valid = (
            choice in criteria
            and isinstance(probabilities, dict)
            and set(probabilities) == set(criteria)
            and all(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1 for value in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[choice] >= max(probabilities.values()) - 1e-6
        )
        if not valid:
            raise ValueError(f"Jev choice {name} is outside the offered criteria")

    @staticmethod
    def _validate_score(name: str, answer: dict, criteria: list) -> None:
        if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
            raise ValueError(f"Jev score {name} has invalid ordered criteria")
        score = answer.get("score")
        legend = answer.get("legend")
        probabilities = answer.get("probabilities")
        confidence = answer.get("confidence")
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(float(score)):
            raise ValueError(f"Invalid Jev score answer {name}")
        score_value = float(score)
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not math.isfinite(float(confidence)):
            raise ValueError(f"Invalid Jev score confidence {name}")
        confidence_value = float(confidence)
        expected_keys = {str(index) for index in range(len(criteria))}
        if not (
            0 <= score_value <= len(criteria) - 1
            and isinstance(legend, dict)
            and set(legend) == expected_keys
            and isinstance(probabilities, dict)
            and set(probabilities) == expected_keys
            and all(type(value) in (int, float) and math.isfinite(float(value)) and 0 <= float(value) <= 1 for value in probabilities.values())
            and 0 <= confidence_value <= 1
            and abs(sum(float(value) for value in probabilities.values()) - 1) < 0.02
            and all(legend[str(index)] == criterion for index, criterion in enumerate(criteria))
        ):
            raise ValueError(f"Jev score {name} is outside the offered criteria")
        weighted = sum(index * float(probabilities[str(index)]) for index in range(len(criteria)))
        if abs(score_value - weighted) > 0.02:
            raise ValueError(f"Jev score {name} is inconsistent with its probability distribution")

    def close(self) -> None:
        """Close the pooled HTTP connection without affecting synthetic transports."""
        with self._connection_lock:
            self._close_connection()

    def __enter__(self) -> "DecisionClient":
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()
