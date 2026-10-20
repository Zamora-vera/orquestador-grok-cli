import httpx
import pytest
from pathlib import Path

from deep_ultra import core
from deep_ultra.core import (MODELS, Cache, ask, cache_key, choose_model, difficulty,
                             run_tests, safe_repo_files)


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.request = httpx.Request('POST', 'https://example.invalid')

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError('provider error', request=self.request, response=self)

    def json(self):
        return self._payload


def reply(text: str = 'ok', prompt: int = 10, completion: int = 2) -> FakeResponse:
    return FakeResponse(payload={'choices': [{'message': {'content': text}}],
                                 'usage': {'prompt_tokens': prompt, 'completion_tokens': completion}})


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test')
    monkeypatch.delenv('KIMI_API_KEY', raising=False)
    monkeypatch.setattr(core, 'RETRY_DELAYS', ())


def test_difficulty():
    assert difficulty('hello') == 0
    assert difficulty('security architecture migration') >= 2


def test_routing(keys):
    assert choose_model('Fix typo').name == 'deepseek-flash'
    assert choose_model('security architecture').name == 'deepseek-v4-pro'
    with pytest.raises(ValueError):
        choose_model('analyze image', vision=True)


def test_routing_vision_with_kimi(keys, monkeypatch):
    monkeypatch.setenv('KIMI_API_KEY', 'test')
    assert choose_model('analyze image', vision=True).name == 'kimi-k2.5'


def test_override_validation(keys):
    assert choose_model('anything', override='deepseek-v4-pro').name == 'deepseek-v4-pro'
    with pytest.raises(ValueError):
        choose_model('anything', override='does-not-exist')
    with pytest.raises(ValueError):
        choose_model('anything', override='kimi-k2.5')


def test_no_keys_available(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.delenv('KIMI_API_KEY', raising=False)
    with pytest.raises(ValueError):
        choose_model('hello')


def test_cache_and_memory(tmp_path: Path):
    cache = Cache(tmp_path / 'db.sqlite3')
    assert cache.get('a') is None
    cache.put('a', 'ok', 10)
    assert cache.get('a') == 'ok'
    cache.put('b', 'expired', -1)
    assert cache.get('b') is None
    assert cache.purge_expired() == 1
    cache.remember('decision', 'go')
    assert cache.recall('decision') == 'go'


def test_safe_file_list(tmp_path: Path):
    (tmp_path / 'a.py').write_text('print(1)')
    (tmp_path / '.git').mkdir()
    (tmp_path / '.git' / 'config').write_text('secret')
    assert safe_repo_files(tmp_path) == ['a.py']


def test_command_allowlist(tmp_path: Path):
    with pytest.raises(ValueError):
        run_tests(tmp_path, ['rm', '-rf', '/'])


def test_key_determinism():
    x = [{'role': 'user', 'content': 'hi'}]
    assert cache_key(x, MODELS[0], 0) == cache_key(x, MODELS[0], 0)
    assert cache_key(x, MODELS[0], 0) != cache_key(x, MODELS[1], 0)
    assert cache_key(x, MODELS[0], 0, 512) != cache_key(x, MODELS[0], 0, 4096)


def test_ask_hits_cache_without_network(keys, tmp_path: Path, monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return reply('hola')

    monkeypatch.setattr(core.httpx, 'post', fake_post)
    cache = Cache(tmp_path / 'db.sqlite3')
    text, model, cached, usage = ask('hello', cache)
    assert (text, model.name, cached) == ('hola', 'deepseek-flash', False)
    assert usage['prompt_tokens'] == 10
    assert calls[0]['json']['model'] == 'deepseek-flash'
    assert calls[0]['json']['max_tokens'] == core.DEFAULT_MAX_TOKENS

    text2, _, cached2, _ = ask('hello', cache)
    assert (text2, cached2) == ('hola', True)
    assert len(calls) == 1


def test_ask_forwards_max_tokens(keys, tmp_path: Path, monkeypatch):
    seen = {}

    def fake_post(url, **kwargs):
        seen.update(kwargs['json'])
        return reply()

    monkeypatch.setattr(core.httpx, 'post', fake_post)
    ask('hello', Cache(tmp_path / 'db.sqlite3'), max_tokens=512)
    assert seen['max_tokens'] == 512


def test_ask_falls_back_within_provider(keys, tmp_path: Path, monkeypatch):
    seen = []

    def fake_post(url, **kwargs):
        model = kwargs['json']['model']
        seen.append(model)
        if model == 'deepseek-v4-pro':
            return FakeResponse(500)
        return reply('fallback ok')

    monkeypatch.setattr(core.httpx, 'post', fake_post)
    text, model, cached, _ = ask('security architecture migration', Cache(tmp_path / 'db.sqlite3'))
    assert text == 'fallback ok'
    assert model.name == 'deepseek-flash'
    assert seen == ['deepseek-v4-pro', 'deepseek-flash']


def test_ask_reports_provider_failure(keys, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(core.httpx, 'post', lambda url, **kwargs: FakeResponse(400))
    with pytest.raises(RuntimeError):
        ask('hello', Cache(tmp_path / 'db.sqlite3'))
