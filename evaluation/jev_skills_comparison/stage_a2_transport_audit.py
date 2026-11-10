"""Read-only Hermes Codex HTTP retry audit; no model request or credentials.

Run inside the installed Hermes Python interpreter to inspect the code actually
imported there. Uses a MockTransport to exercise the physical-request guard;
it does not authenticate, contact any service, or authorize live dispatch.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import json
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace


def source_binding(module) -> dict:
    path = Path(inspect.getfile(module)).resolve(strict=True)
    return {"file": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def inspect_outer_retries(func) -> int | None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Tuple):
            for idx, elt in enumerate(node.targets):
                if isinstance(elt, ast.Tuple):
                    for pos, name in enumerate(elt.elts):
                        if isinstance(name, ast.Name) and name.id == "max_stream_retries":
                            value = node.value.elts[pos]
                            if isinstance(value, ast.Constant) and type(value.value) is int:
                                return value.value
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "max_stream_retries" for t in node.targets):
                if isinstance(node.value, ast.Constant) and type(node.value.value) is int:
                    return node.value.value
    return None


def sdk_retry_zero_from_source(method) -> bool:
    source = inspect.getsource(method)
    tree = ast.parse(textwrap.dedent(source))
    return any(
        isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        and type(node.value.value) is int and node.value.value == 0
        and any(isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                and target.value.id == "request_kwargs" and isinstance(target.slice, ast.Constant)
                and target.slice.value == "max_retries" for target in node.targets)
        for node in ast.walk(tree)
    )


def mock_http_guard(worker, httpx) -> dict:
    sent = []

    def respond(request):
        sent.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(respond)
    with httpx.Client(transport=transport, follow_redirects=True) as client:
        fake_sdk = SimpleNamespace(max_retries=0, _client=client)
        with worker.one_http_post(fake_sdk, "gpt-6-luna") as count:
            response = client.post("https://chatgpt.com/backend-api/codex/responses")
            assert response.status_code == 200
            try:
                client.post("https://chatgpt.com/backend-api/codex/responses")
            except RuntimeError as exc:
                if "Second physical HTTP request blocked" not in str(exc):
                    raise
            else:
                raise RuntimeError("Unblocked second HTTP request")
        assert count["requests"] == 1 and len(sent) == 1
    return {"first_mock_http_success": True, "second_mock_http_blocked_before_send": True,
            "actual_http_requests": 0, "simulated_http_requests_sent": len(sent)}


def audit() -> dict:
    import httpx
    import agent.codex_runtime as codex_runtime
    import agent.client_lifecycle as lifecycle
    import agent.chat_completion_helpers as helpers

    worker_file = Path(__file__).with_name("stage_a2_native_worker.py")
    spec = importlib.util.spec_from_file_location("stage_a2_native_worker_for_audit", worker_file)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    retries = inspect_outer_retries(codex_runtime.run_codex_stream)
    sdk_zero = sdk_retry_zero_from_source(lifecycle.ClientLifecycleMixin._create_request_openai_client)
    bridge_source = inspect.getsource(helpers._dispatch_nonstreaming_api_request)
    request_scoped_bridge = 'client=make_client("codex_stream_request")' in bridge_source
    mock = mock_http_guard(worker, httpx)
    passing = sdk_zero and request_scoped_bridge and retries in (0, 1) and mock["second_mock_http_blocked_before_send"]
    return {
        "status": "offline_guard_confirmed" if passing else "blocked_runtime_incompatible",
        "sources": {"codex_runtime": source_binding(codex_runtime),
                    "client_lifecycle": source_binding(lifecycle),
                    "chat_completion_helpers": source_binding(helpers),
                    "worker": {"file": str(worker_file.resolve()), "sha256": hashlib.sha256(worker_file.read_bytes()).hexdigest()}},
        "outer_codex_retries_configured": retries,
        "sdk_request_scoped_max_retries_zero_in_source": sdk_zero,
        "codex_uses_request_scoped_sdk_client": request_scoped_bridge,
        "mock_transport": mock,
        "external_requests_executed": 0,
        "live_native_requests_verified": False,
        "provider_effective_model_independently_verified": False,
        "live_dispatch_authorized": False,
    }


if __name__ == "__main__":
    try:
        result = audit()
    except Exception as exc:
        print(json.dumps({"status": "blocked_audit_error", "exception_class": type(exc).__name__,
                          "external_requests_executed": 0, "live_dispatch_authorized": False}))
        raise SystemExit(2)
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["status"] == "offline_guard_confirmed" else 2)
