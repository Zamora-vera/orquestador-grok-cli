import pytest

from deep_ultra.agents import PROFILES, agent_prompt, run_agent, select_profile
from deep_ultra.catalog import MODELS
from deep_ultra.core import Answer


def test_profile_selection():
    assert select_profile('arregla el bug del login') == 'debugger'
    assert select_profile('revisa la seguridad y los permisos') == 'security'
    assert select_profile('propone la arquitectura del módulo') == 'architect'
    assert select_profile('agrega cobertura de tests') == 'tester'
    assert select_profile('implementa una feature') == 'coder'
    assert select_profile('hola') == 'coder'


def test_agent_prompt_includes_task_and_context(tmp_path):
    from deep_ultra.context import build_pack
    pack = build_pack(tmp_path, 1000)
    text = agent_prompt('revisa esto', pack)
    assert 'revisa esto' in text and 'Project context' in text


def test_run_agent_uses_profile_system(tmp_path):
    seen = {}

    def fake_ask(prompt, cache, **kwargs):
        seen.update(kwargs)
        seen['prompt'] = prompt
        return Answer('ok', MODELS[0], True, {}, 0.0, 0.0, 0.0, 'off_peak')

    chosen, pack, answer = run_agent('auto', 'arregla el bug', tmp_path, cache=None, ask_func=fake_ask)
    assert chosen == 'debugger'
    assert seen['system'] == PROFILES['debugger']['system']
    assert seen['task'] == 'agent:debugger'
    assert answer.text == 'ok'
    assert pack['files'] == 0


def test_unknown_profile(tmp_path):
    with pytest.raises(ValueError):
        run_agent('nope', 'x', tmp_path, cache=None, ask_func=lambda *a, **k: None)
