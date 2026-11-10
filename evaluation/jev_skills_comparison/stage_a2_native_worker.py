"""One isolated no-tools Codex Hermes request for Stage A2.

Only launched by stage_a2_collector. Uses Hermes' documented native runtime
source seam, not the production gateway or a Switchyard plugin hook. Provider
credentials stay in the owner-selected authentication scope; they are not
copied into the disposable test home or emitted in a receipt.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import sys
import time
from pathlib import Path

APPROVED = ("gpt-6.1-sol", "gpt-6-luna")
OUTPUT_POLICY = "codex_posthoc_observed_output_tokens_le_128"
OUTPUT_ACCEPTANCE_MAX = 128
RUNTIME_MODULES = (
    "run_agent", "hermes_state", "hermes_constants",
    "hermes_cli.runtime_provider", "hermes_cli.env_loader", "agent.secret_scope",
    "agent.client_lifecycle", "agent.codex_runtime", "agent.chat_completion_helpers",
)


def loaded_runtime_hash(root: Path) -> str:
    root = root.resolve(strict=True)
    data = {}
    for name in RUNTIME_MODULES:
        module = sys.modules.get(name)
        origin = getattr(module, "__file__", None)
        if not isinstance(origin, str):
            raise RuntimeError("Hermes runtime module unavailable: " + name)
        path = Path(origin).resolve(strict=True)
        if path != root / (name.replace(".", "/") + ".py") or not path.is_relative_to(root):
            raise RuntimeError("Hermes loaded code differs from approved runtime: " + name)
        data[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@contextmanager
def one_http_post(client, model: str, *, output_cap: int = 128):
    """Count actual HTTP requests, not just ``_run_codex_stream`` invocations.

    Hermes currently permits one outer Codex stream reconnect. The OpenAI SDK
    request-scoped client must already set max_retries=0. The sync httpx hook
    rejects *any* second physical request before it can be dispatched, whether
    it came from Hermes, the SDK, redirects, or an unexpected client feature.
    Reject unknown transports instead of guessing that the wire count is one.
    """
    if type(getattr(client, "max_retries", None)) is not int or client.max_retries != 0:
        raise RuntimeError("OpenAI SDK retries not explicitly disabled")
    transport = getattr(client, "_client", None)
    hooks = getattr(transport, "event_hooks", None)
    if not isinstance(hooks, dict) or not isinstance(hooks.get("request"), list):
        raise RuntimeError("Cannot enforce physical HTTP request limit")
    count = {"requests": 0}

    def allow_one(request):
        # Do not leak URLs/tokens/prompts in exceptions or worker output.
        if count["requests"]:
            raise RuntimeError("Second physical HTTP request blocked")
        url = getattr(request, "url", None)
        path = str(getattr(url, "path", "") or "")
        host = str(getattr(url, "host", "") or "").lower()
        if (getattr(request, "method", None) != "POST" or host != "chatgpt.com"
                or path.rstrip("/") != "/backend-api/codex/responses"):
            raise RuntimeError("Unexpected Codex HTTP destination or method")
        count["requests"] += 1

    hooks["request"].append(allow_one)
    try:
        yield count
    finally:
        hooks["request"].remove(allow_one)


def check_wire_payload(payload: dict, model: str) -> None:
    """Require the Codex backend's real no-cap wire format; threshold is post-response."""
    if (not isinstance(payload, dict) or payload.get("model") != model
            or payload.get("tools") not in (None, [])
            or "max_output_tokens" in payload
            or payload.get("previous_response_id") is not None):
        raise RuntimeError("Outbound Codex model/tool contract mismatch")


def enforce_single_application_attempt(agent) -> None:
    """Reduce Hermes' outer retry loop to one attempt in this isolated worker."""
    if type(getattr(agent, "_api_max_retries", None)) is not int:
        raise RuntimeError("Unknown Hermes application retry setting")
    agent._api_max_retries = 1
    if agent._api_max_retries != 1:
        raise RuntimeError("Hermes application retries not disabled")


def main(args) -> None:
    line = sys.stdin.readline()
    job = json.loads(line)
    if sys.stdin.readline() != "":
        raise RuntimeError("Only one job per child process is permitted")
    attempt_id = job.get("attempt_id")
    model_id = job.get("model")
    prompt = job.get("prompt")
    if (not isinstance(attempt_id, str) or not attempt_id.startswith("a2-") or
            model_id not in tuple("openai-codex:" + m for m in APPROVED) or
            not isinstance(prompt, str) or not 0 < len(prompt) <= 2000 or
            job.get("output_token_acceptance_max") != OUTPUT_ACCEPTANCE_MAX or
            job.get("output_policy") != OUTPUT_POLICY):
        raise RuntimeError("Invalid single-request public study contract")
    model = model_id.split(":", 1)[1]
    # Verify the imported modules, including the source that owns native retries.
    import agent.client_lifecycle, agent.codex_runtime, agent.chat_completion_helpers
    from hermes_cli.env_loader import hydrate_profile_secret_sources
    from agent.secret_scope import build_profile_secret_scope, set_secret_scope
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from hermes_constants import set_hermes_home_override
    from run_agent import AIAgent
    from hermes_state import SessionDB
    # No untrusted source path is imported from the evaluation checkout.
    runtime_sha = loaded_runtime_hash(args.hermes_root)
    auth_home = args.auth_home.resolve(strict=True)
    if os.environ.get("HERMES_HOME") != str(auth_home):
        raise RuntimeError("Authentication scope is not the explicitly selected profile")
    hydrate_profile_secret_sources(auth_home)
    set_secret_scope(build_profile_secret_scope(auth_home))
    runtime = resolve_runtime_provider(requested="openai-codex", target_model=model)
    if not (runtime.get("provider") == "openai-codex" and
            runtime.get("api_mode") == "codex_responses" and runtime.get("api_key")):
        raise RuntimeError("Requested Codex provider is not available in this credential scope")

    home = args.home.resolve(strict=True)
    if any(home.iterdir()):
        raise RuntimeError("Per-attempt home must start empty")
    for name in ("workspace", "skills", "plugins", "empty-bundled"):
        (home / name).mkdir(mode=0o700)
    config = {"model": {"provider": "openai-codex", "default": model},
              "plugins": {"enabled": [], "entries": {}},
              "memory": {"memory_enabled": False, "user_profile_enabled": False},
              "terminal": {"cwd": str(home / "workspace")}}
    import hermes_yaml as yaml
    (home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    os.environ["HERMES_HOME"] = str(home)
    os.environ["HERMES_BUNDLED_PLUGINS"] = str(home / "empty-bundled")
    set_hermes_home_override(str(home))

    db = SessionDB(home / "state.db")
    agent = None
    try:
        agent = AIAgent(
            model=model, provider="openai-codex", requested_provider="openai-codex",
            base_url=runtime.get("base_url"), api_key=runtime["api_key"],
            api_mode=runtime["api_mode"], credential_pool=None, fallback_model=None,
            max_iterations=1, max_tokens=128, quiet_mode=True, verbose_logging=False, enabled_toolsets=[],
            reasoning_config={"effort": "low"}, platform="cli",
            session_id="stage-a2-" + attempt_id, session_db=db,
            skip_context_files=True, load_soul_identity=False,
            skip_memory=True, skip_background_review=True,
            cwd=str(home / "workspace"), run_budget_seconds=90,
        )
        agent.tools = []
        agent.valid_tool_names = set()
        enforce_single_application_attempt(agent)
        if getattr(agent, "_fallback_chain", None):
            raise RuntimeError("Unexpected model fallback configured")
        call_models = []
        physical_requests = []
        native = agent._run_codex_stream

        def guarded_stream(payload, **kwargs):
            if len(call_models) >= 1:
                raise RuntimeError("Second logical model request forbidden")
            check_wire_payload(payload, model)
            client = kwargs.get("client")
            if client is None:
                raise RuntimeError("Native request client missing: SDK retries unverified")
            # The request-scoped SDK client is constructed by Hermes with
            # max_retries=0. A hook guards the lower httpx request boundary.
            with one_http_post(client, model) as observed:
                call_models.append(payload["model"])
                try:
                    return native(payload, **kwargs)
                finally:
                    physical_requests.append(observed["requests"])

        agent._run_codex_stream = guarded_stream
        started = time.perf_counter()
        result = agent.run_conversation(prompt, task_id=attempt_id, conversation_history=[])
        elapsed_ms = (time.perf_counter() - started) * 1000
        if (len(call_models) != 1 or physical_requests != [1]
                or not result.get("completed")):
            raise RuntimeError("Expected exactly one physically observed HTTP request")
        # Fields such as served_model/request_id are optional runner observations,
        # not independently authenticated provider receipts.
        safe = {"attempt_id": attempt_id, "provider": "openai-codex",
                "requested_model": model, "wire_model": call_models[0],
                "effective_model": result.get("served_model") if isinstance(result.get("served_model"), str) else None,
                "physical_call_count": physical_requests[0], "fallback_enabled": False,
                "tools_enabled": False, "completed": True,
                "output_policy": OUTPUT_POLICY, "output_token_acceptance_max": OUTPUT_ACCEPTANCE_MAX,
                "provider_output_cap_enforced": False,
                "answer": result.get("final_response"), "elapsed_ms": elapsed_ms,
                "input_tokens": result.get("input_tokens"),
                "output_tokens": result.get("output_tokens"),
                "provider_request_id": result.get("request_id"),
                "runtime_modules_sha256": runtime_sha}
        print("RESULT " + json.dumps(safe, allow_nan=False), flush=True)
    finally:
        if agent is not None:
            agent.close()
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--auth-home", type=Path, required=True)
    parser.add_argument("--hermes-root", type=Path, required=True)
    try:
        main(parser.parse_args())
    except Exception as exc:
        # No exception details: secrets or private paths could be present.
        print("WORKER_ERROR " + type(exc).__name__, flush=True)
        sys.exit(2)
