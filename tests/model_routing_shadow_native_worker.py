"""Installed-artifact shadow routing through the native request owner, offline."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import socket
import sys
import zipfile
from typing import Any


def main():
    archive, scenario = sys.argv[1:3]
    home = Path(os.environ["HERMES_HOME"])
    assert os.environ["HERMES_PROFILE"] == "default"
    assert home.is_relative_to(Path(os.environ["TMPDIR"]))
    socket.socket.connect = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("No sockets"))
    if scenario == "shadow-native-named":
        home = home / "profiles/fixture-named"
        home.mkdir(parents=True)
        os.environ["HERMES_HOME"] = str(home)
    installed = home / "plugins/hermes-switchyard"
    installed.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        source.extractall(installed)
    assert (installed / "hermes_switchyard/model_routing_shadow.py").is_file()
    sys.path.insert(0, str(installed))
    import yaml
    from hermes_cli.plugins import get_plugin_manager
    from hermes_cli.middleware import apply_llm_request_middleware

    mode = "off" if scenario == "shadow-off" else "apply" if scenario == "shadow-apply" else "shadow"
    task = "Explain this public synthetic CSV example."
    settings = {
        "automatic_skill_recommendation": False,
        "adaptive_reasoning_effort": True,
        "adaptive_reasoning_effort_mode": "pinned",
        "adaptive_reasoning_effort_receipt_mode": "off",
        "shared_jev_skill_effort": False,
        "public_or_sanitized_data_ack": True,
        "approved_model_registry": [{
            "id": "public-small", "provider": "openai-codex", "model": "gpt-6-luna",
            "account": "fixture-account", "approved": True, "cost": 1,
            "data_classes_allowed": ["public"], "tool_capabilities": [],
            "context_limit": 120000, "description": "Public synthetic tasks",
        }],
        "approved_model_registry_version": "fixture-v1",
        "approved_model_registry_valid_until": "2100-01-01T00:00:00Z",
    }
    if scenario != "shadow-off":
        settings["model_routing_mode"] = mode
    (home / "config.yaml").write_text(yaml.safe_dump({"plugins": {
        "enabled": ["hermes-switchyard"], "entries": {"hermes-switchyard": {"settings": settings}},
    }}))
    manager = get_plugin_manager()
    manager.discover_and_load(force=True)
    loaded = manager._plugins.get("hermes-switchyard")
    assert loaded is not None and loaded.enabled and loaded.error is None, manager.list_plugins()
    entrypoint: Any = loaded.module
    assert entrypoint is not None
    plugin: Any = sys.modules[entrypoint.register.__module__]
    assert Path(plugin.__file__).resolve().is_relative_to(installed)
    client_module = sys.modules[plugin.__name__ + ".client"]
    original_client, expected_model = client_module.DecisionClient, client_module.EXPECTED_MODEL
    sys.modules[plugin.__name__ + ".egress_redaction"]._reset_for_tests(lambda value: value, loaded=True)
    credential = {"value": "fixture-key"}
    plugin._secret = lambda provider="auto": credential["value"] if provider in {"auto", "openrouter"} else ""
    calls = []

    def transport(payload):
        calls.append(payload)
        assert all(name.startswith("fit_") for name in payload["questions"]), payload
        if scenario == "shadow-cancel-inflight":
            manager.invoke_hook("agent_loop_stopped", session_key="public-session", reason="cancelled")
        return {"model": expected_model, "request_id": "fixture-model-1", "usage": {},
                "answers": {name: {"noul": 0.95} for name in payload["questions"]}}

    plugin.DecisionClient = lambda **_kwargs: original_client(api_key="fixture-key", model=expected_model, transport=transport)
    assert len(manager._middleware["llm_request"]) == 1
    assert len(manager.iter_hook_callbacks("pre_llm_call")) == 1
    controller: Any = getattr(manager._middleware["llm_request"][0], "__self__")
    observer: Any = controller.model_shadow
    context = {
        "session_id": "public-session", "task_id": "public-task", "turn_id": "public-turn",
        "api_request_id": "public-request", "base_url": "https://api.example.com/v1",
        "model": "gpt-6-sol", "provider": "openai-codex", "api_mode": "codex_responses",
        "platform": "cli", "parent_session_id": None,
        "manual_model_pinned": False, "model_account": "fixture-account",
    }
    if scenario == "shadow-pin":
        context["manual_model_pinned"] = True
    if scenario == "shadow-unknown-pin":
        context.pop("manual_model_pinned")
    if scenario == "shadow-private":
        task = "Company Confidential\nPrivate synthetic task."
    manager.invoke_hook("pre_llm_call", user_message=task, **context)
    if scenario not in {"shadow-unavailable", "shadow-private"}:
        # This explicit pre-existing tool operation is the only Jev request.
        args = {"task": task, "requirements": {"data_classes": ["public"]}}
        if scenario == "shadow-unknown-pin":
            args["manual_model_pinned"] = False  # Tool arguments do not confer pin provenance.
        result = json.loads(plugin._REGISTERED_HANDLERS["jev_model_route_approved"](args, **context))
        assert result["status"] == "selected", result
        assert len(calls) == 1
    if scenario == "shadow-provider":
        context["provider"] = "another-provider"
    if scenario == "shadow-account":
        context["model_account"] = "another-account"
    if scenario == "shadow-identity":
        credential["value"] = "fixture-altered"
    if scenario == "shadow-cancel":
        manager.invoke_hook("agent_loop_stopped", session_key="public-session", reason="cancelled")
    if scenario == "shadow-stale":
        observer.pending[observer.key(context)]["completed"] -= 31
    if scenario == "shadow-observer-failure":
        observer.observe = lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("fixture observer failure"))
    request = {"model": "gpt-6-sol", "input": [{"role": "user", "content": task}], "reasoning": {"effort": "high"}}
    original = copy.deepcopy(request)
    before = len(calls)
    if scenario.startswith("shadow-native-"):
        from native_request_probe import build_native_request
        if scenario in {"shadow-native-pin", "shadow-native-clear"}:
            from gateway.config import GatewayConfig, Platform
            from gateway.session import SessionSource, SessionStore
            store = SessionStore(home / "sessions", GatewayConfig())
            entry = store.get_or_create_session(SessionSource(platform=Platform.LOCAL, chat_id="fixture-cli"))
            store.set_model_override(entry.session_key, {"model": "gpt-6-sol", "provider": "openai-codex"})
            assert store.get_model_override(entry.session_key) is not None
            if scenario == "shadow-native-clear":
                store.set_model_override(entry.session_key, None)
                assert store.get_model_override(entry.session_key) is None
        outgoing, native = build_native_request(request, context, pinned=scenario == "shadow-native-pin")
        assert "manual_model_pinned" not in native and "model_account" not in native
        if scenario == "shadow-native-provider":
            changed = {**context, "provider": "another-provider", "api_request_id": "retry-request"}
            first_route = observer.receipts[-1]["native_provenance"]["route_sha256"]
            outgoing, native = build_native_request(request, changed)
            assert observer.receipts[-1]["native_provenance"]["route_sha256"] != first_route
        if scenario == "shadow-native-account":
            first_route = observer.receipts[-1]["native_provenance"]["route_sha256"]
            outgoing, native = build_native_request(request, {**context, "api_request_id": "account-retry"}, credential_entry="another-fixture-entry")
            assert observer.receipts[-1]["native_provenance"]["route_sha256"] == first_route
            assert "provider_account" in observer.receipts[-1]["native_provenance"]["missing"]
        if scenario == "shadow-native-missing":
            outgoing, native = build_native_request(request, {**context, "api_request_id": ""})
            assert observer.receipts[-1]["reason"] == "native_request_provenance_incomplete"
        if scenario == "shadow-native-conflict":
            outgoing, native = build_native_request(request, {**context, "session_id": "rotated-session"})
            assert observer.receipts[-1]["reason"] == "native_attempt_conflict"
        if scenario == "shadow-native-cancel-retry":
            manager.invoke_hook("agent_loop_stopped", session_key="public-session", reason="cancelled")
            outgoing, native = build_native_request(request, context)
            assert observer.receipts[-1]["reason"] == "native_attempt_cancelled"
            outgoing, native = build_native_request(request, {**context, "api_request_id": "fresh-retry"})
            assert observer.receipts[-1]["reason"] == "pin_provenance_unavailable"
        if scenario == "shadow-native-named":
            assert observer.receipts[-1]["native_provenance"]["profile"] == "fixture-named"
        assert outgoing == original and request == original
    else:
        dispatched = apply_llm_request_middleware(request, **context)
        assert dispatched.payload == original and request == original
    assert len(calls) == before  # The shadow observer has no network/client seam.
    receipt = observer.receipts[-1] if observer else None
    if scenario == "shadow-unqualified":
        assert receipt is not None
        assert receipt["status"] == "abstained" and receipt["proposed"] is None, receipt
        assert receipt["reason"] == "pin_provenance_unavailable", receipt
        assert receipt["native_provenance"]["source"] == "hermes-request-context"
        assert len(calls) == 1
    elif scenario == "shadow-off":
        assert observer is None
    else:
        assert receipt is not None
        assert receipt["status"] == "abstained", receipt
    if receipt:
        assert receipt["applied"] is False and receipt["can_apply"] is False
        assert receipt["new_jev_requests"] == 0
    print(json.dumps({
        "scenario": scenario, "installed_module": str(Path(plugin.__file__).relative_to(home)),
        "fixture_requests": len(calls), "shadow_receipt": receipt,
        "request_unchanged": True, "request_owner_count": len(manager._middleware["llm_request"]),
        "pre_hook_count": len(manager.iter_hook_callbacks("pre_llm_call")),
        "default_off": observer is None, "network": "socket.connect blocked",
    }))


if __name__ == "__main__":
    main()
