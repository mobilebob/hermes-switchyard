"""Exercise Hermes' real request builder without transport or credential resolution.

Only payload preparation/cache decoration are fixture seams. The native builder
itself supplies all middleware context; no synthetic pin/account kwargs are added.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest import mock


def build_native_request(request, context, *, pinned=False, credential_entry="fixture-entry"):
    from agent.turn_api_request import build_api_request
    from hermes_cli import middleware

    original = middleware.apply_llm_request_middleware
    observed = {}

    def tap(payload, **native):
        observed.update(native)
        return original(payload, **native)

    agent = SimpleNamespace(
        model=context["model"], provider=context["provider"], base_url=context["base_url"],
        api_mode=context["api_mode"], session_id=context["session_id"], platform="cli",
        tools=[], max_tokens=1024, _empty_content_retries=0, _is_user_initiated_turn=False,
        # Fresh text-only native agent state; keep the real image sanitizer.
        _image_rejecting_models=set(),
        _explicit_model_override=pinned, _credential_pool_entry_id=credential_entry,
        _reset_stream_delivery_tracking=lambda: None,
        _reapply_reasoning_echo_for_provider=lambda *_args: None,
        _build_api_kwargs=lambda *_a, **_k: copy.deepcopy(request),
        _get_transport=lambda: SimpleNamespace(preflight_kwargs=lambda payload, **_kw: payload),
        _is_copilot_url=lambda: False, _is_codex_backend=lambda: True,
    )
    with mock.patch("hermes_cli.middleware.apply_llm_request_middleware", side_effect=tap), \
         mock.patch("agent.turn_api_request.sanitize_outbound_kwargs", lambda *_a: None), \
         mock.patch("agent.conversation_loop._redecorate_prompt_cache_for_provider",
                    lambda _agent, messages, **kw: (messages, None, kw["tools_for_api"])):
        result = build_api_request(
            agent, api_messages=request["input"], _moa_prepared_request=None, tools_for_api=[],
            system_message="", messages=request["input"], original_user_message=request["input"][0]["content"],
            approx_tokens=16, total_chars=64, retry_count=context.get("retry_count", 0),
            api_call_count=context.get("api_call_count", 1), api_request_id=context["api_request_id"],
            api_start_time=0, effective_task_id=context["task_id"], turn_id=context["turn_id"],
        )
    return result.api_kwargs, observed
