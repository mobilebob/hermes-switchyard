"""All four opt-ins in one installed native process; synthetic transport only."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from unittest import mock


def memory_case(question):
    cards = [
        {"session_id": "public-a", "title": "Public archive", "snippet": "Telescope aperture forty centimetres.", "match_message_id": "message-a"},
        {"session_id": "public-b", "title": "Public archive", "snippet": "Telescope aperture sixty centimetres.", "match_message_id": "message-b"},
    ]
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
    policy = {"epoch": "fixture-v1", "profile": "default", "queries": {digest(question): "public"},
              "sources": {digest(card): "public" for card in cards}}
    return cards, policy


def exercise(manager, plugin, settings, question, source, tool, args, result, home):
    from hermes_cli.middleware import apply_llm_request_middleware
    import model_tools
    from tools.registry import registry
    client_module = sys.modules[plugin.__name__ + '.client']
    calls = []

    def transport(payload):
        calls.append(copy.deepcopy(payload))
        answers = {}
        for name, spec in payload['questions'].items():
            if spec['type'] == 'choice':
                keys = list(spec['criteria'])
                selected = next((key for key in ('csv-skill', 'low', 'sufficient') if key in keys), keys[-1])
                others = [key for key in keys if key != selected]
                probabilities = {key: .01/len(others) for key in others}
                probabilities[selected] = .99 if others else 1.0
                answers[name] = {'choice': selected, 'confidence': max(probabilities.values()), 'probabilities': probabilities}
            else:
                answers[name] = {'noul': .99 if name == 'needs_skill' or 'relevance' in name else .01}
        return {'model': 'jev-1.14', 'answers': answers, 'request_id': f'fixture-integrated-{len(calls)}'}

    plugin._secret = lambda provider='auto': 'fixture-key'
    plugin.DecisionClient = lambda **_kw: client_module.DecisionClient(
        api_key='fixture-key', endpoint=client_module.TYPESAFE_ENDPOINT, model='jev-latest', transport=transport)
    assert len(manager._middleware.get('llm_request', [])) == 1
    assert len(manager._middleware.get('tool_execution', [])) == 1
    assert len(manager.iter_hook_callbacks('pre_llm_call')) == 2
    assert len(manager.iter_hook_callbacks('transform_tool_result')) == 1
    invalidators = [cb for cb in manager.iter_hook_callbacks('agent_loop_stopped') if cb.__name__ == 'invalidate_decisions']
    assert len(invalidators) == 1
    owners = next(cell.cell_contents for cell in invalidators[0].__closure__ if isinstance(cell.cell_contents, list))
    assert len(owners) == 4
    broker = next(owner for owner in owners if owner.__class__.__name__ == 'SharedDecisionBroker')
    memory = next(owner for owner in owners if owner.__class__.__name__ == 'MemoryScreenShadow')
    web = next(owner for owner in owners if owner.__class__.__name__ == 'WebScreenShadow')
    route = next(owner for owner in owners if owner.__class__.__name__ == 'ModelRoutingShadow')
    context = dict(session_id='fixture-session', task_id='fixture-task', turn_id='fixture-turn',
                   platform='cli', model='gpt-6-sol', parent_session_id=None, user_message=question)
    loaded = manager.invoke_hook('pre_llm_call', **context)
    assert any('PUBLIC INTEGRATED SKILL BODY' in str(value) for value in loaded), loaded
    assert len(calls) == 1 and 'reasoning_effort_high' in calls[0]['questions'], calls
    wire = {'model': 'gpt-6-sol', 'input': [{'role': 'user', 'content': question}], 'reasoning': {'effort': 'high'}}
    original = copy.deepcopy(wire)
    output = apply_llm_request_middleware(wire, provider='openai-codex', api_mode='codex_responses', **context).payload
    expected = copy.deepcopy(original)
    expected['reasoning']['effort'] = 'low'
    assert output == expected and wire == original, (output, wire)
    assert len(calls) == 1 and len(broker.receipts) == 1
    assert route.status['last_receipt']['applied'] is False
    assert route.status['last_receipt']['new_jev_requests'] == 0

    # Native dispatch traverses the memory scope binder and web result observer.
    cards, _policy = memory_case(question)
    before_cards = copy.deepcopy(cards)
    search = json.loads(model_tools.handle_function_call(
        'jev_session_search_rerank', {'query': question, 'candidates': cards},
        session_id=context['session_id'], task_id=context['task_id'], turn_id=context['turn_id'],
        tool_call_id='fixture-memory-call', enabled_tools=['jev_session_search_rerank']))
    assert 'error' not in search, search
    assert len(calls) == 2 and 'session' in calls[-1]['questions'], calls
    assert any(key.startswith('memory_') for key in calls[-1]['questions'])
    assert memory.receipts[-1]['judged'] > 0 and cards == before_cards
    assert not web.receipts  # The unrelated retrieval result triggers no web decision.
    raw = json.dumps(result, ensure_ascii=False)
    with mock.patch.object(registry, 'dispatch', return_value=raw), mock.patch.object(model_tools, '_READ_SEARCH_TOOLS', frozenset()):
        returned = model_tools.handle_function_call(tool, args, session_id=context['session_id'],
            task_id=context['task_id'], turn_id=context['turn_id'], tool_call_id='fixture-web-call')
        assert returned == raw
    assert len(calls) == 3 and calls[-1]['state']['contract'] == 'web_screen.shadow.v1'
    assert web.receipts[-1]['judged'] > 0
    assert route.status['last_receipt']['applied'] is False
    manager.invoke_hook('agent_loop_stopped', session_key='fixture-session', reason='cancelled')
    assert not broker.records and not broker.generations
    print(json.dumps({'scenario': 'web-integrated', 'installed_module': str(plugin.__file__).removeprefix(str(home)+'/'),
        'fixture_requests': len(calls), 'default_off': False, 'mutated': False, 'network': 'socket.connect blocked',
        'llm_request_owners': 1, 'shared_consumptions': len(broker.receipts),
        'memory_extra_requests': 0, 'model_shadow_extra_requests': 0, 'web_extra_requests': 1,
        'wire_changes': ['reasoning.effort'], 'model_shadow_applied': False}))
