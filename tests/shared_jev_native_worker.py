"""Fresh-process packaged-plugin lifecycle probe; synthetic data, no sockets."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import zipfile


def main():
    archive, scenario = sys.argv[1:3]
    if scenario.startswith("shadow-"):
        from model_routing_shadow_native_worker import main as shadow_main
        return shadow_main()
    home = Path(os.environ["HERMES_HOME"])
    assert home.is_relative_to(Path(os.environ["TMPDIR"]))
    assert os.environ.get("HERMES_PROFILE") == "default"
    socket.socket.connect = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("No sockets"))
    installed = home / "plugins/hermes-switchyard"
    installed.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        source.extractall(installed)
    assert (installed / "hermes_switchyard/combined_decisions.py").is_file()
    sys.path.insert(0, str(installed))
    import yaml
    import hermes_switchyard as plugin
    from hermes_switchyard import egress_redaction
    from hermes_switchyard.client import DecisionClient, EXPECTED_MODEL
    from hermes_cli.plugins import get_plugin_manager
    from hermes_cli.middleware import apply_llm_request_middleware

    assert Path(plugin.__file__).resolve().is_relative_to(installed)
    egress_redaction._reset_for_tests(lambda value: value, loaded=True)
    task = "Please explain this public synthetic CSV example clearly."
    names = ["csv-skill", "log-skill"]
    if scenario == "large":
        names += [f"filler-skill-{number:03d}" for number in range(401)]
    for name in names[:2]:
        directory = home / "skills" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Analyze public CSV data\n---\nPUBLIC SKILL BODY {name}\n"
        )
    settings = {
        "automatic_skill_candidates": [{"name": name, "description": "Analyze public CSV data"} for name in names],
        "automatic_skill_routing_mode": "hosted_sanitized",
        "automatic_skill_consumer_mode": "load",
        "automatic_skill_cache_seconds": 0,
        "automatic_skill_light_turn_bypass": False,
        "automatic_skill_deadline_seconds": 2.0,
        "adaptive_reasoning_effort": True,
        "reasoning_effort_receipt_mode": "off",
        "public_or_sanitized_data_ack": True,
        "automatic_skill_public_or_sanitized_data_ack": True,
        "automatic_skill_two_stage": scenario in {"large", "stage-two", "stage-two-failure"},
    }
    if scenario != "default-off":
        settings["shared_jev_skill_effort"] = True
    if scenario in {"stage-two", "stage-two-failure"}:
        settings.update(automatic_skill_hosted_detail="descriptions")
    (home / "config.yaml").write_text(yaml.safe_dump({"plugins": {"enabled": ["hermes-switchyard"], "entries": {"hermes-switchyard": {"settings": settings}}}}))
    calls = []
    lock = threading.Lock()
    entered, release = threading.Event(), threading.Event()
    route = {"generation": "fixture"}
    brokers = []
    def transport(payload):
        with lock:
            number = len(calls) + 1
            calls.append(copy.deepcopy(payload))
        if scenario in {"cancel", "overlap", "unload", "concurrent-inflight"} and number == 1:
            entered.set()
            assert release.wait(5), "blocked fixture was not released"
        if scenario == "route-inflight" and number == 1:
            route["generation"] = "changed"
        if scenario == "outage" or (scenario == "retry" and number == 1):
            raise RuntimeError("Synthetic dispatch failure")
        if scenario == "deadline" and number == 1:
            time.sleep(0.45)
        if scenario in {"stage-two-failure", "failed-stage-two-after-shared"} and payload["state"].get("stage") == 2:
            raise RuntimeError("Synthetic stage-two failure")
        answers = {}
        for name, question in payload["questions"].items():
            if question["type"] == "noul":
                answers[name] = {"noul": (0.01 if name == "stakes" or scenario == "no-skill" else 0.99)}
            else:
                keys = list(question["criteria"])
                wanted = "low" if name.startswith("reasoning_effort") else "csv-skill"
                selected = wanted if wanted in keys else "__jev_none_of_these__" if "__jev_none_of_these__" in keys else keys[0]
                rest = [key for key in keys if key != selected]
                probabilities = {key: 0.01 / len(rest) for key in rest}
                probabilities[selected] = 0.99 if rest else 1.0
                answers[name] = {"choice": selected, "confidence": max(probabilities.values()), "probabilities": probabilities}
        return {"model": EXPECTED_MODEL, "answers": answers, "usage": {}, "request_id": f"fixture-{number}"}

    def client_factory(**_kwargs):
        return DecisionClient(api_key="fixture-key", model=EXPECTED_MODEL, transport=transport, timeout=2)

    manager = get_plugin_manager()
    manager.discover_and_load(force=True)
    loaded = manager._plugins.get("hermes-switchyard")
    assert loaded is not None and loaded.enabled and loaded.error is None, manager.list_plugins()
    # Directory plugins are imported under the native owner's private namespace.
    # Patch only the installed owner's fixture seam, never the checkout copy.
    plugin = sys.modules[loaded.module.register.__module__]
    assert Path(plugin.__file__).resolve().is_relative_to(installed)
    plugin.DecisionClient = client_factory
    plugin._secret = lambda provider="auto": "fixture-key" if provider in {"auto", "openrouter"} else ""
    sys.modules[plugin.__name__ + ".egress_redaction"]._reset_for_tests(lambda value: value, loaded=True)
    assert len(manager._middleware.get("llm_request", [])) == 1
    controller = manager._middleware["llm_request"][0].__self__
    if controller.shared_decisions is not None:
        brokers.append(controller.shared_decisions)
        brokers[0].identity_factory = lambda: dict(route)
    assert manager._middleware.get("llm_request")
    assert plugin._RUNTIME_STATUS["shared_jev_skill_effort"] is (scenario != "default-off")
    context = dict(session_id="public-session", task_id="public-task", turn_id="public-turn", model="gpt-6-sol", platform="cli", parent_session_id=None, user_message=task)
    if scenario == "private":
        task = "Company Confidential\nPrivate synthetic CSV example."
        context["user_message"] = task

    def pre(ctx=None):
        return manager.invoke_hook("pre_llm_call", **(context if ctx is None else ctx))

    def request(ctx=None):
        return apply_llm_request_middleware(
            {"model": "gpt-6-sol", "input": [{"role": "user", "content": task}], "reasoning": {"effort": "high"}},
            provider="openai-codex", api_mode="codex_responses", **(context if ctx is None else ctx),
        ).payload

    contexts = []
    other_loaded = False
    if scenario in {"cancel", "overlap", "unload", "concurrent-inflight"}:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(pre)
            assert entered.wait(3)
            if scenario == "unload":
                manager.unload()
            elif scenario == "cancel":
                manager.invoke_hook("agent_loop_stopped", session_key="public-session", reason="cancelled")
            elif scenario == "concurrent-inflight":
                other = {**context, "session_id": "other-session", "task_id": "other-task", "turn_id": "other-turn"}
                second = pool.submit(pre, other)
                # The native hook owner may serialize or skip an overlapping
                # invocation. Do not deadlock the fixture waiting for dispatch.
                release.set()
                second_context = second.result(timeout=3)
                contexts.extend(second_context)
                other_loaded = any("PUBLIC SKILL BODY" in str(value) for value in second_context)
                other_wire = request(other)
                assert other_wire["reasoning"]["effort"] == "low"
                assert len(brokers[0].receipts) == int(other_loaded)
            else:
                # Supersede the in-flight producer for exactly the same turn.
                second = pool.submit(pre)
                contexts.extend(second.result(timeout=3))
            release.set()
            contexts.extend(first.result(timeout=3))
    elif scenario == "concurrent":
        with ThreadPoolExecutor(max_workers=2) as pool:
            other = {**context, "session_id": "other-session", "task_id": "other-task", "turn_id": "other-turn"}
            results = list(pool.map(pre, (context, other)))
            contexts = results[0] + results[1]
        first_wire = request()
        second_wire = request(other)
        assert len(brokers[0].receipts) == 2
        assert first_wire["reasoning"]["effort"] == second_wire["reasoning"]["effort"] == "low"
    else:
        if scenario == "route-before":
            route["generation"] = "before"
        contexts = pre()
    if scenario in {"stage-two-after-shared", "failed-stage-two-after-shared"}:
        # Native pre-call produced a valid shared record. Exercise the installed
        # facade's stage-two bypass while retaining that same producer scope.
        combined = sys.modules[plugin.__name__ + ".combined_decisions"]
        key = (context["session_id"], context["task_id"], context["turn_id"])
        record = brokers[0].records[key]
        token = combined._SCOPE.set({"key": key, "task": task, "generation": record["generation"]})
        try:
            facade = combined.SharedSkillClient(client_factory(), brokers[0])
            stage_two_failed = False
            try:
                facade.decide({"stage": 2, "task": task}, {
                    "skill": {"type": "choice", "instructions": "Pick a public CSV skill", "criteria": {"csv-skill": "CSV", "log-skill": "Logs"}},
                    "needs_skill": {"type": "noul", "instructions": "Does this public task need a skill?"},
                }, public_or_sanitized_data_ack=True)
            except Exception:
                stage_two_failed = True
            assert stage_two_failed == (scenario == "failed-stage-two-after-shared")
        finally:
            combined._SCOPE.reset(token)
        assert len(calls) == 2 and len(calls[-1]["questions"]) == 2
    if scenario == "route-after":
        route["generation"] = "after"
    if scenario == "stale":
        for record in brokers[0].records.values():
            record["at"] -= 31
    first_wire = request()
    shared_count = len(brokers[0].receipts) if brokers else 0
    if scenario == "retry":
        next_context = {**context, "turn_id": "retry-turn"}
        contexts.extend(pre(next_context))
        request(next_context)
        assert len(brokers[0].receipts) == 1
    if scenario == "consume-once":
        before = shared_count
        request()
        assert len(brokers[0].receipts) == before == 1
    if scenario in {"cancel", "unload"}:
        assert shared_count == 0, "cancelled in-flight response became shareable"
    if scenario in {"route-after", "route-inflight", "stale", "deadline", "private", "outage", "default-off"}:
        assert shared_count == 0, (scenario, shared_count)
    if scenario in {"shared", "route-before", "consume-once", "no-skill"}:
        assert shared_count == 1, (scenario, calls)
        assert len(calls) == 1
    if scenario == "outage":
        assert first_wire["reasoning"]["effort"] == "high"
        assert len(calls) == 2
    if scenario == "private":
        assert not calls
        assert first_wire["reasoning"]["effort"] == "high"
    if scenario == "default-off":
        assert not brokers
        assert len(calls) == 2
    if scenario not in {"no-skill", "private", "outage", "stage-two-failure"}:
        assert any("PUBLIC SKILL BODY" in str(value) for value in contexts), contexts
    if scenario in {"stage-two", "stage-two-failure"}:
        assert any(call["state"].get("stage") == 2 for call in calls), calls
        # The automatic multi-stage selector is deliberately not consolidated.
        assert shared_count == 0, (scenario, shared_count)
        assert len(calls) == 3, (scenario, len(calls))
    if scenario in {"stage-two-after-shared", "failed-stage-two-after-shared"}:
        assert shared_count == 1 and len(calls) == 2
    if scenario == "large":
        assert len(calls) > 2 and any(call["state"].get("stage") == 2 for call in calls), calls
    if scenario == "concurrent-inflight":
        assert shared_count == 1 + int(other_loaded) and len(calls) == 2
        ids = [receipt["shared_request_id"] for receipt in brokers[0].receipts]
        assert len(ids) == len(set(ids))
    if scenario == "overlap":
        # Native dispatch rejects a second invocation of a still-running hook.
        # There is no second producer to supersede the first result.
        assert shared_count == 1 and len(calls) == 1
    if scenario == "no-skill":
        assert not any("PUBLIC SKILL BODY" in str(value) for value in contexts)
    result = {"scenario": scenario, "installed_module": str(Path(plugin.__file__).relative_to(home)), "fixture_requests": len(calls), "question_counts": [len(call["questions"]) for call in calls], "stages": [call["state"].get("stage") for call in calls], "shared_receipts": len(brokers[0].receipts) if brokers else 0, "effort": first_wire["reasoning"]["effort"], "default_off": not brokers, "network": "socket.connect blocked"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
