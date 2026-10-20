import os
import pytest
from pathlib import Path
from deep_ultra.core import Cache, choose_model, difficulty, safe_repo_files, run_tests, MODELS, cache_key


def test_difficulty():
    assert difficulty('hello') == 0
    assert difficulty('security architecture migration') >= 2


def test_routing(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test')
    monkeypatch.delenv('KIMI_API_KEY', raising=False)
    assert choose_model('Fix typo').name == 'deepseek-chat'
    assert choose_model('security architecture').name == 'deepseek-reasoner'
    with pytest.raises(ValueError):
        choose_model('analyze image', vision=True)


def test_cache_and_memory(tmp_path):
    cache = Cache(tmp_path / 'db.sqlite3')
    assert cache.get('a') is None
    cache.put('a', 'ok', 10)
    assert cache.get('a') == 'ok'
    cache.put('b', 'expired', -1)
    assert cache.get('b') is None
    cache.remember('decision', 'go')
    assert cache.recall('decision') == 'go'


def test_safe_file_list(tmp_path):
    (tmp_path / 'a.py').write_text('print(1)')
    (tmp_path / '.git').mkdir()
    (tmp_path / '.git' / 'config').write_text('secret')
    assert safe_repo_files(tmp_path) == ['a.py']


def test_command_allowlist(tmp_path):
    with pytest.raises(ValueError):
        run_tests(tmp_path, ['rm', '-rf', '/'])


def test_key_determinism():
    x = [{'role': 'user', 'content': 'hi'}]
    assert cache_key(x, MODELS[0], 0) == cache_key(x, MODELS[0], 0)
    assert cache_key(x, MODELS[0], 0) != cache_key(x, MODELS[1], 0)
