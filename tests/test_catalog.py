import datetime as dt

import pytest

from deep_ultra.catalog import META, MODELS, get
from deep_ultra.core import choose_model
from deep_ultra.finops import PricingUnavailable

UTC = dt.timezone.utc
OFF_PEAK = dt.datetime(2026, 10, 19, 5, 0, tzinfo=UTC)


def test_catalog_is_dated():
    assert META['checked_at'] == '2026-10-20'
    assert META['sources']['deepseek'].startswith('https://api-docs.deepseek.com')


def test_flash_capabilities_are_verified():
    flash = get('deepseek-flash')
    assert flash.context == 1_000_000
    assert flash.vision is True
    assert flash.pricing_verified is True
    assert flash.price('peak').input == pytest.approx(0.30)
    assert flash.price('off_peak').input == pytest.approx(0.15)


def test_pro_is_a_distinct_model_without_vision():
    flash, pro = get('deepseek-flash'), get('deepseek-v4-pro')
    assert pro.version != flash.version
    assert pro.vision is False
    assert pro.price('peak').output == pytest.approx(3.96)


def test_kimi_prices_stay_unverified():
    kimi = [m for m in MODELS if m.provider == 'kimi']
    assert kimi
    assert all(m.pricing_verified is False and m.pricing is None for m in kimi)


def test_router_prefers_deepseek(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test')
    monkeypatch.setenv('KIMI_API_KEY', 'test')
    assert choose_model('security architecture', at=OFF_PEAK).provider == 'deepseek'
    assert choose_model('hello', at=OFF_PEAK).id == 'deepseek-flash'


def test_unverified_prices_block_kimi(monkeypatch):
    monkeypatch.setenv('KIMI_API_KEY', 'test')
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    with pytest.raises(ValueError):
        choose_model('hello', at=OFF_PEAK)
