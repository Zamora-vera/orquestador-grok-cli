import datetime as dt

import pytest

from deep_ultra import finops
from deep_ultra.finops import (Budget, BudgetExceeded, Ledger, LoopDetected,
                               actual_cost, estimate_cost, is_peak, next_off_peak, tariff)
from deep_ultra.catalog import MODELS, get

UTC = dt.timezone.utc
FLASH = get('deepseek-flash', MODELS)
PRO = get('deepseek-v4-pro', MODELS)


def test_peak_windows_utc():
    assert is_peak(dt.datetime(2026, 10, 19, 2, 0, tzinfo=UTC))      # Monday 02:00
    assert is_peak(dt.datetime(2026, 10, 19, 9, 59, tzinfo=UTC))     # Monday 09:59
    assert not is_peak(dt.datetime(2026, 10, 19, 5, 0, tzinfo=UTC))  # gap between windows
    assert not is_peak(dt.datetime(2026, 10, 19, 10, 0, tzinfo=UTC))
    assert not is_peak(dt.datetime(2026, 10, 18, 2, 0, tzinfo=UTC))  # Sunday
    assert tariff(dt.datetime(2026, 10, 19, 2, 0, tzinfo=UTC)) == 'peak'
    assert tariff(dt.datetime(2026, 10, 19, 5, 0, tzinfo=UTC)) == 'off_peak'


def test_chinese_holiday_is_off_peak():
    # 2026-02-17 is Chinese New Year (Tuesday).
    assert not is_peak(dt.datetime(2026, 2, 17, 2, 0, tzinfo=UTC))


def test_next_off_peak():
    assert next_off_peak(dt.datetime(2026, 10, 19, 2, 0, tzinfo=UTC)) == dt.datetime(
        2026, 10, 19, 4, 0, tzinfo=UTC)
    assert next_off_peak(dt.datetime(2026, 10, 19, 7, 0, tzinfo=UTC)) == dt.datetime(
        2026, 10, 19, 10, 0, tzinfo=UTC)
    at = dt.datetime(2026, 10, 19, 5, 0, tzinfo=UTC)
    assert next_off_peak(at) == at


def test_estimate_cost_uses_tariff():
    at_peak = dt.datetime(2026, 10, 19, 2, 0, tzinfo=UTC)
    at_off = dt.datetime(2026, 10, 19, 5, 0, tzinfo=UTC)
    assert estimate_cost(FLASH, 1_000_000, 0, at_peak) == pytest.approx(0.30)
    assert estimate_cost(FLASH, 1_000_000, 0, at_off) == pytest.approx(0.15)
    assert estimate_cost(PRO, 1_000_000, 0, at_peak) == pytest.approx(1.32)


def test_actual_cost_with_provider_cache():
    at = dt.datetime(2026, 10, 19, 2, 0, tzinfo=UTC)
    usage = {'prompt_tokens': 1000, 'prompt_cache_hit_tokens': 800,
             'prompt_cache_miss_tokens': 200, 'completion_tokens': 100}
    cost, cache_saving, offpeak_saving = actual_cost(FLASH, usage, at)
    assert cost == pytest.approx((800 * 0.006 + 200 * 0.30 + 100 * 1.2) / 1_000_000)
    assert cache_saving == pytest.approx(800 * (0.30 - 0.006) / 1_000_000)
    assert offpeak_saving == 0

    off_cost, _, off_saving = actual_cost(FLASH, usage, dt.datetime(2026, 10, 19, 5, 0, tzinfo=UTC))
    assert off_saving == pytest.approx(cost - off_cost)


def test_budget_blocks_task_and_daily(tmp_path):
    ledger = Ledger(__import__('sqlite3').connect(tmp_path / 'l.db'))
    at = dt.datetime(2026, 10, 19, 5, 0, tzinfo=UTC)
    budget = Budget(task_usd=0.001, daily_usd=0.002)
    ledger.check('t', 'hello', 0.0005, budget, at)
    ledger.record(task='t', model='deepseek-flash', usage={'prompt_tokens': 1, 'completion_tokens': 1},
                  cost=0.0009, saved_real=0, saved_est=0, at=at, cache_hit=False)
    with pytest.raises(BudgetExceeded):
        ledger.check('t', 'hello', 0.0005, budget, at)


def test_loop_guard_calls_and_repeats(tmp_path):
    import sqlite3
    ledger = Ledger(sqlite3.connect(tmp_path / 'l.db'))
    at = dt.datetime(2026, 10, 19, 5, 0, tzinfo=UTC)
    budget = Budget(max_calls_per_task=1, max_repeats=2)
    ledger.check('t', 'one', 0, budget, at)
    ledger.bump_attempt('t', Ledger.hash_prompt('one'))
    with pytest.raises(LoopDetected):
        ledger.check('t', 'two', 0, budget, at)
    budget = Budget(max_calls_per_task=10, max_repeats=1)
    ledger.bump_attempt('u', Ledger.hash_prompt('same'))
    with pytest.raises(LoopDetected):
        ledger.check('u', 'same', 0, budget, at)


def test_report_separates_real_and_estimated_savings(tmp_path):
    import sqlite3
    ledger = Ledger(sqlite3.connect(tmp_path / 'l.db'))
    at = dt.datetime(2026, 10, 19, 5, 0, tzinfo=UTC)
    ledger.record(task='t', model='deepseek-flash', usage={'prompt_tokens': 10, 'completion_tokens': 2},
                  cost=0.01, saved_real=0, saved_est=0.002, at=at, cache_hit=False)
    ledger.record(task='t', model='deepseek-flash', usage={}, cost=0, saved_real=0.01,
                  saved_est=0, at=at, cache_hit=True)
    report = ledger.report(30, at)
    assert report['events'] == 2
    assert report['cache_hits'] == 1
    assert report['saved_real_usd'] == pytest.approx(0.01)
    assert report['saved_est_usd'] == pytest.approx(0.002)
    assert report['cost_usd'] == pytest.approx(0.01)


def test_task_and_daily_alerts(tmp_path):
    import sqlite3
    ledger = Ledger(sqlite3.connect(tmp_path / 'l.db'))
    at = dt.datetime(2026, 10, 19, 5, 0, tzinfo=UTC)
    ledger.record(task='job', model='deepseek-flash', usage={'prompt_tokens': 1, 'completion_tokens': 1},
                  cost=0.009, saved_real=0, saved_est=0, at=at, cache_hit=False)
    alerts = ledger.alerts(Budget(task_usd=0.01, daily_usd=0.01, alert_pct=0.8), at, 'job')
    assert any("task 'job'" in message for message in alerts)
    assert any('daily' in message for message in alerts)
