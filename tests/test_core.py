import datetime as dt

import httpx
import pytest

from deep_ultra import core
from deep_ultra.catalog import MODELS, get
from deep_ultra.core import (Cache, DeferredWork, ask, cache_key, choose_model, difficulty,
                             run_tests, safe_repo_files)
from deep_ultra.finops import Budget, BudgetExceeded, LoopDetected, PricingUnavailable

FLASH = get('deepseek-flash', MODELS)
PRO = get('deepseek-v4-pro', MODELS)
OFF_PEAK = dt.datetime(2026, 10, 19, 5, 0, tzinfo=dt.timezone.utc)


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
    assert choose_model('Fix typo').id == 'deepseek-flash'
    assert choose_model('security architecture').id == 'deepseek-v4-pro'


def test_vision_routes_to_flash_not_kimi(keys, monkeypatch):
    monkeypatch.setenv('KIMI_API_KEY', 'test')  # Kimi is available but costly
    assert choose_model('describe this image', vision=True).id == 'deepseek-flash'


def test_override_validation(keys, monkeypatch):
    assert choose_model('anything', override='deepseek-v4-pro').id == 'deepseek-v4-pro'
    with pytest.raises(ValueError):
        choose_model('anything', override='does-not-exist')
    with pytest.raises(ValueError):
        choose_model('anything', override='kimi-k3')  # no key
    with pytest.raises(ValueError):
        choose_model('anything', vision=True, override='deepseek-v4-pro')
    monkeypatch.setenv('KIMI_API_KEY', 'test')
    with pytest.raises(PricingUnavailable):
        choose_model('anything', override='kimi-k3')


def test_no_keys_available(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.delenv('KIMI_API_KEY', raising=False)
    with pytest.raises(ValueError):
        choose_model('hello')


def test_cache_and_memory(tmp_path):
    cache = Cache(tmp_path / 'db.sqlite3')
    assert cache.get('a') is None
    cache.put('a', 'ok', 10)
    assert cache.get('a') == 'ok'
    cache.put('b', 'expired', -1)
    assert cache.get('b') is None
    assert cache.purge_expired() == 1
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
    assert cache_key(x, FLASH, 0) == cache_key(x, FLASH, 0)
    assert cache_key(x, FLASH, 0) != cache_key(x, PRO, 0)
    assert cache_key(x, FLASH, 0, 512) != cache_key(x, FLASH, 0, 4096)


def test_ask_records_cost_and_hits_cache(keys, tmp_path, monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs['json'])
        return reply('hola')

    monkeypatch.setattr(core.httpx, 'post', fake_post)
    cache = Cache(tmp_path / 'db.sqlite3')
    first = ask('hello', cache, now=OFF_PEAK)
    assert (first.text, first.model.id, first.cached) == ('hola', 'deepseek-flash', False)
    assert first.cost_usd > 0
    assert calls[0]['model'] == 'deepseek-flash'
    assert calls[0]['max_tokens'] == core.DEFAULT_MAX_TOKENS

    second = ask('hello', cache, now=OFF_PEAK)
    assert second.cached is True
    assert second.usage == {}
    assert second.saved_real_usd == pytest.approx(first.cost_usd)
    assert len(calls) == 1
    report = cache.ledger.report(30, OFF_PEAK)
    assert report['cache_hits'] == 1
    assert report['cost_usd'] == pytest.approx(first.cost_usd)


def test_ask_forwards_max_tokens(keys, tmp_path, monkeypatch):
    seen = {}

    def fake_post(url, **kwargs):
        seen.update(kwargs['json'])
        return reply()

    monkeypatch.setattr(core.httpx, 'post', fake_post)
    ask('hello', Cache(tmp_path / 'db.sqlite3'), max_tokens=512, now=OFF_PEAK)
    assert seen['max_tokens'] == 512


def test_ask_falls_back_within_provider(keys, tmp_path, monkeypatch):
    seen = []

    def fake_post(url, **kwargs):
        model = kwargs['json']['model']
        seen.append(model)
        if model == 'deepseek-v4-pro':
            return FakeResponse(500)
        return reply('fallback ok')

    monkeypatch.setattr(core.httpx, 'post', fake_post)
    answer = ask('security architecture migration', Cache(tmp_path / 'db.sqlite3'), now=OFF_PEAK)
    assert answer.text == 'fallback ok'
    assert answer.model.id == 'deepseek-flash'
    assert seen == ['deepseek-v4-pro', 'deepseek-flash']


def test_budget_blocks_before_network(keys, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(core.httpx, 'post', lambda url, **kw: calls.append(kw) or reply())
    cache = Cache(tmp_path / 'db.sqlite3')
    budget = Budget(task_usd=0.000001)
    with pytest.raises(BudgetExceeded):
        ask('hello', cache, budget=budget, now=OFF_PEAK)
    assert calls == []


def test_loop_guard_blocks_extra_calls(keys, tmp_path, monkeypatch):
    monkeypatch.setattr(core.httpx, 'post', lambda url, **kw: reply())
    cache = Cache(tmp_path / 'db.sqlite3')
    budget = Budget(max_calls_per_task=1)
    ask('first prompt', cache, budget=budget, now=OFF_PEAK)
    with pytest.raises(LoopDetected):
        ask('second prompt', cache, budget=budget, now=OFF_PEAK)


def test_defer_refuses_beyond_max_wait(keys, tmp_path, monkeypatch):
    peak = dt.datetime(2026, 10, 19, 2, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(core.httpx, 'post', lambda url, **kw: reply())
    with pytest.raises(DeferredWork) as exc:
        ask('hello', Cache(tmp_path / 'db.sqlite3'), defer=True, max_wait=0, now=peak)
    assert exc.value.wait_seconds == 2 * 3600


def test_defer_sleeps_then_calls(keys, tmp_path, monkeypatch):
    peak = dt.datetime(2026, 10, 19, 2, 0, tzinfo=dt.timezone.utc)
    slept = []
    monkeypatch.setattr(core, 'next_off_peak', lambda at: at + dt.timedelta(minutes=1))
    monkeypatch.setattr(core.time, 'sleep', lambda seconds: slept.append(seconds))
    monkeypatch.setattr(core.httpx, 'post', lambda url, **kw: reply())
    answer = ask('hello', Cache(tmp_path / 'db.sqlite3'), defer=True, max_wait=120, now=peak)
    assert answer.text == 'ok'
    assert slept == [60]


def test_ask_reports_provider_failure(keys, tmp_path, monkeypatch):
    monkeypatch.setattr(core.httpx, 'post', lambda url, **kwargs: FakeResponse(400))
    with pytest.raises(RuntimeError):
        ask('hello', Cache(tmp_path / 'db.sqlite3'), now=OFF_PEAK)
