"""Exercise an installed web observer through native result dispatch, offline."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import socket
import sys
from typing import Any
from unittest import mock
import zipfile


def main():
    archive, scenario = sys.argv[1:3]
    home = Path(os.environ['HERMES_HOME'])
    assert home.is_relative_to(Path(os.environ['TMPDIR']))
    socket.socket.connect = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('No sockets'))
    installed = home / 'plugins/hermes-switchyard'
    installed.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        source.extractall(installed)
    assert (installed / 'hermes_switchyard/web_screening.py').is_file()
    sys.path.insert(0, str(installed))
    import yaml
    from hermes_switchyard.web_screening import digest
    from hermes_cli.plugins import get_plugin_manager
    question = 'What is the public telescope mirror aperture?'
    source = {'url': 'https://example.org/public-aperture', 'title': 'Public archive',
              'description': 'The public telescope mirror aperture is forty centimetres.'}
    tool = 'web_extract' if scenario == 'web-extract' else 'web_search'
    args = {'urls': [source['url']]} if tool == 'web_extract' else {'query': question}
    if tool == 'web_extract':
        source['content'] = source.pop('description')
    result = {'results': [source]} if tool == 'web_extract' else {'success': True, 'data': {'web': [source]}}
    approval = {'data_class': 'public', 'access': 'public_unauthenticated'}
    policy = {'epoch': 'fixture-v1', 'profile': 'default',
              'scope': {'session_id': 'fixture-session', 'task_id': 'fixture-task', 'turn_id': 'fixture-turn'},
              'question': question, 'queries': {digest(question): 'public'},
              'sources': {digest(source): dict(approval)}}
    if scenario == 'web-private':
        policy['sources'][digest(source)]['data_class'] = 'private'
    if scenario == 'web-policy-missing':
        policy = {}
    settings = {'automatic_skill_recommendation': False, 'adaptive_reasoning_effort': False,
                'shared_jev_skill_effort': False, 'model_routing_mode': 'off',
                'public_or_sanitized_data_ack': scenario != 'web-no-consent',
                'jev_provider': 'typesafe', 'jev_model': 'jev-latest',
                'web_screen_policy': policy}
    if scenario != 'web-off':
        settings['web_screen_mode'] = 'apply' if scenario == 'web-apply' else 'shadow'
    if scenario == 'web-coexist':
        settings.update(repeated_output_compaction=True, cross_tool_stuck_detection=True)
    (home/'config.yaml').write_text(yaml.safe_dump({'plugins': {'enabled': ['hermes-switchyard'],
        'entries': {'hermes-switchyard': {'settings': settings}}}}))
    manager = get_plugin_manager()
    manager.discover_and_load(force=True)
    loaded = manager._plugins['hermes-switchyard']
    assert loaded.enabled and loaded.error is None, loaded.error
    assert loaded.module is not None
    plugin: Any = sys.modules[loaded.module.register.__module__]
    assert Path(plugin.__file__).is_relative_to(installed)
    module = sys.modules[plugin.__name__ + '.web_screening']
    client_module = sys.modules[plugin.__name__ + '.client']
    sys.modules[plugin.__name__ + '.egress_redaction']._reset_for_tests(lambda value: value, loaded=True)
    assert not manager.has_middleware('llm_request')
    assert not manager._middleware.get('tool_execution')
    assert len(manager.iter_hook_callbacks('pre_llm_call')) == (1 if scenario == 'web-coexist' else 0)
    off = scenario in {'web-off', 'web-apply', 'web-no-consent'}
    hooks = manager.iter_hook_callbacks('transform_tool_result')
    assert len(hooks) == (0 if off else 1), len(hooks)
    owner: Any = None
    for callback in manager.iter_hook_callbacks('agent_loop_stopped'):
        if callback.__name__ == 'invalidate_decisions':
            for cell in callback.__closure__ or ():
                if isinstance(cell.cell_contents, list):
                    owner = next((x for x in cell.cell_contents if isinstance(x, module.WebScreenShadow)), None)
    assert (owner is None) == off
    calls = []
    def transport(payload):
        calls.append(copy.deepcopy(payload))
        assert payload['state']['contract'] == 'web_screen.shadow.v1'
        if scenario == 'web-outage':
            raise RuntimeError('synthetic outage')
        if scenario == 'web-cancel':
            manager.invoke_hook('agent_loop_stopped', session_id='fixture-session', task_id='fixture-task', turn_id='fixture-turn')
        if scenario == 'web-consent-revoke':
            # Revoke the exact live configuration owner read by the observer.
            plugin.ctx_get_config = lambda _ctx, key, default=None: False if key == 'public_or_sanitized_data_ack' else settings.get(key, default)
        if scenario == 'web-source-change':
            policy['epoch'] = 'fixture-v2'
            owner.policy_factory = lambda: policy
        answers = {}
        for name, spec in payload['questions'].items():
            if spec['type'] == 'choice':
                answers[name] = {'choice': 'sufficient', 'confidence': .98,
                    'probabilities': {'sufficient': .98, 'insufficient': .01, 'conflicting': .01}}
            else:
                answers[name] = {'noul': .9 if name.startswith('relevance_') else .1}
        return {'model': 'jev-1.14', 'answers': answers, 'request_id': 'fixture-web-request', 'usage': {}, 'latency_ms': 1.0}
    plugin._secret = lambda provider='auto': 'fixture-key'
    plugin.DecisionClient = lambda **_kw: client_module.DecisionClient(api_key='fixture-key',
        endpoint=client_module.TYPESAFE_ENDPOINT, model='jev-latest', transport=transport)
    if scenario == 'web-observer-failure':
        owner._observe = lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError('synthetic observer failure'))
    if scenario == 'web-local-rejection':
        result = {'error': 'local security rejection'}
    raw = json.dumps(result, ensure_ascii=False)
    import model_tools
    from tools.registry import registry
    if scenario == 'web-missing-scope':
        turn = None
    else:
        turn = 'fixture-turn'
    with mock.patch.object(registry, 'dispatch', return_value=raw), mock.patch.object(model_tools, '_READ_SEARCH_TOOLS', frozenset()):
        output = model_tools.handle_function_call(tool, args, session_id='fixture-session',
            task_id='fixture-task', turn_id=turn, tool_call_id='fixture-call')
        assert output == raw, (output, raw)
        if scenario == 'web-duplicate':
            assert model_tools.handle_function_call(tool, args, session_id='fixture-session',
                task_id='fixture-task', turn_id=turn, tool_call_id='fixture-call') == raw
    receipt = owner.receipts[-1] if owner and owner.receipts else None
    if scenario == 'web-duplicate':
        assert len(calls) == 1 and receipt['reason'] == 'duplicate_invocation'
        assert any(item['reason'] == 'observed' and item['judged'] > 0 for item in owner.receipts)
    positive = scenario in {'web-search', 'web-extract', 'web-coexist'}
    if positive:
        assert len(calls) == 1, calls
        assert receipt['judged'] > 0 and receipt['observations']
        assert receipt['evidence'] == 'sufficient'
    elif scenario in {'web-outage', 'web-cancel', 'web-consent-revoke', 'web-source-change'}:
        assert len(calls) == 1, calls
        assert receipt['judged'] == 0 and not receipt['observations'], receipt
    elif scenario != 'web-duplicate':
        assert len(calls) == 0, calls
    assert plugin._RUNTIME_STATUS['web_screen']['applied'] is False
    assert plugin._RUNTIME_STATUS['web_screen']['filter_available'] is False
    if receipt:
        encoded = json.dumps(receipt)
        assert question not in encoded and source['url'] not in encoded
    print(json.dumps({'scenario': scenario, 'installed_module': str(Path(plugin.__file__).relative_to(home)),
        'fixture_requests': len(calls), 'default_off': off, 'mutated': False,
        'reason': receipt['reason'] if receipt else 'off', 'network': 'socket.connect blocked'}))


if __name__ == '__main__':
    main()
