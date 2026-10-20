"""Cost engine: tariffs, budgets, ledger, alerts and loop protection.

Prices come from the dated catalog (never hardcoded here). Peak/off-peak windows
follow DeepSeek's published policy: peak is 01:00-04:00 and 06:00-10:00 UTC,
Monday to Friday, excluding Chinese public holidays. Everything else is off-peak.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import sqlite3
from dataclasses import dataclass

from .catalog import ModelSpec

PEAK_WINDOWS = ((1, 4), (6, 10))
PEAK_TARIFF = 'peak'
OFF_PEAK_TARIFF = 'off_peak'

try:  # Optional: holiday accuracy degrades to "no holiday" when missing.
    import holidays as _holidays
except ImportError:  # pragma: no cover - dependency is declared, this is a safety net
    _holidays = None

_HOLIDAY_CACHE: dict[int, frozenset[dt.date]] = {}


class BudgetExceeded(RuntimeError):
    """Raised before a call that would cross a configured budget."""


class LoopDetected(RuntimeError):
    """Raised when one task repeats the same prompt or exceeds its call cap."""


class PricingUnavailable(RuntimeError):
    """Raised when a model has no verified price and cannot be costed."""


def _holidays_for(year: int) -> frozenset[dt.date]:
    if year not in _HOLIDAY_CACHE:
        if _holidays is None:
            _HOLIDAY_CACHE[year] = frozenset()
        else:
            _HOLIDAY_CACHE[year] = frozenset(_holidays.CN(years=[year]).keys())
    return _HOLIDAY_CACHE[year]


def is_peak(at: dt.datetime) -> bool:
    """True inside a DeepSeek peak window (UTC, weekdays, non-holiday)."""
    at = at.astimezone(dt.timezone.utc)
    if at.weekday() >= 5 or at.date() in _holidays_for(at.year):
        return False
    return any(start <= at.hour < end for start, end in PEAK_WINDOWS)


def tariff(at: dt.datetime) -> str:
    return PEAK_TARIFF if is_peak(at) else OFF_PEAK_TARIFF


def next_off_peak(at: dt.datetime) -> dt.datetime:
    """First instant at off-peak rates. Off-peak already started returns ``at``."""
    at = at.astimezone(dt.timezone.utc)
    if not is_peak(at):
        return at
    end = next(end for start, end in PEAK_WINDOWS if start <= at.hour < end)
    return at.replace(hour=end, minute=0, second=0, microsecond=0)


def estimate_tokens(text: str) -> int:
    """Conservative character-based estimate; refine with provider usage when billed."""
    return max(1, len(text) // 4)


def estimate_cost(model: ModelSpec, prompt_tokens: int, max_output: int, at: dt.datetime) -> float:
    price = model.price(tariff(at))
    if price is None:
        raise PricingUnavailable(f'No verified price for {model.id}')
    return (prompt_tokens * price.input + max_output * price.output) / 1_000_000


def _cached_tokens(usage: dict) -> int:
    if 'prompt_cache_hit_tokens' in usage:
        return int(usage.get('prompt_cache_hit_tokens') or 0)
    details = usage.get('prompt_tokens_details') or {}
    return int(details.get('cached_tokens') or 0)


def actual_cost(model: ModelSpec, usage: dict, at: dt.datetime) -> tuple[float, float, float]:
    """Return (cost, provider_cache_saving, off_peak_saving). All amounts in USD."""
    now_price = model.price(tariff(at))
    if now_price is None:
        raise PricingUnavailable(f'No verified price for {model.id}')
    prompt = int(usage.get('prompt_tokens') or 0)
    cached = _cached_tokens(usage)
    miss = int(usage.get('prompt_cache_miss_tokens') or max(prompt - cached, 0))
    completion = int(usage.get('completion_tokens') or 0)
    cost = (cached * now_price.cache_hit + miss * now_price.input + completion * now_price.output) / 1_000_000
    cache_saving = cached * max(now_price.input - now_price.cache_hit, 0.0) / 1_000_000
    peak_price = model.price(PEAK_TARIFF)
    peak_cost = 0.0 if peak_price is None else (
        cached * peak_price.cache_hit + miss * peak_price.input + completion * peak_price.output) / 1_000_000
    return cost, cache_saving, max(peak_cost - cost, 0.0)


@dataclass(frozen=True)
class Budget:
    task_usd: float = 0.0
    daily_usd: float = 0.0
    alert_pct: float = 0.8
    max_calls_per_task: int = 25
    max_repeats: int = 3

    @classmethod
    def from_env(cls) -> 'Budget':
        def number(name: str, default: float) -> float:
            raw = os.getenv(name)
            return float(raw) if raw else default
        return cls(
            task_usd=number('DEEP_ULTRA_TASK_BUDGET_USD', 0.0),
            daily_usd=number('DEEP_ULTRA_DAILY_BUDGET_USD', 0.0),
            alert_pct=number('DEEP_ULTRA_ALERT_PCT', 0.8),
            max_calls_per_task=int(number('DEEP_ULTRA_MAX_CALLS_PER_TASK', 25)),
            max_repeats=int(number('DEEP_ULTRA_MAX_REPEATS', 3)),
        )

    def describe(self) -> dict:
        return {'task_usd': self.task_usd, 'daily_usd': self.daily_usd,
                'alert_pct': self.alert_pct, 'max_calls_per_task': self.max_calls_per_task,
                'max_repeats': self.max_repeats}


class Ledger:
    """SQLite ledger shared with the cache database."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.execute('''CREATE TABLE IF NOT EXISTS usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, task TEXT NOT NULL,
            model TEXT NOT NULL, prompt_tokens INTEGER NOT NULL, cached_tokens INTEGER NOT NULL,
            completion_tokens INTEGER NOT NULL, cost_usd REAL NOT NULL,
            saved_real_usd REAL NOT NULL, saved_est_usd REAL NOT NULL,
            tariff TEXT NOT NULL, cache_hit INTEGER NOT NULL)''')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS calls (
            task TEXT NOT NULL, prompt_hash TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (task, prompt_hash))''')
        self.conn.commit()

    @staticmethod
    def hash_prompt(prompt: str) -> str:
        return hashlib.sha256(prompt.encode('utf-8')).hexdigest()

    def spent_task(self, task: str) -> float:
        row = self.conn.execute('SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE task=?', (task,)).fetchone()
        return float(row[0])

    def spent_since(self, since: dt.datetime) -> float:
        row = self.conn.execute('SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE ts>=?',
                                (since.timestamp(),)).fetchone()
        return float(row[0])

    def attempts(self, task: str, prompt_hash: str) -> int:
        row = self.conn.execute('SELECT n FROM calls WHERE task=? AND prompt_hash=?',
                                (task, prompt_hash)).fetchone()
        return int(row[0]) if row else 0

    def bump_attempt(self, task: str, prompt_hash: str) -> None:
        self.conn.execute('''INSERT INTO calls (task, prompt_hash, n) VALUES (?, ?, 1)
            ON CONFLICT(task, prompt_hash) DO UPDATE SET n = n + 1''', (task, prompt_hash))
        self.conn.commit()

    def task_calls(self, task: str) -> int:
        row = self.conn.execute('SELECT COALESCE(SUM(n), 0) FROM calls WHERE task=?', (task,)).fetchone()
        return int(row[0])

    def check(self, task: str, prompt: str, estimate_usd: float, budget: Budget, at: dt.datetime) -> None:
        prompt_hash = self.hash_prompt(prompt)
        if budget.max_calls_per_task and self.task_calls(task) >= budget.max_calls_per_task:
            raise LoopDetected(f'Task {task!r} reached {budget.max_calls_per_task} model calls')
        if budget.max_repeats and self.attempts(task, prompt_hash) >= budget.max_repeats:
            raise LoopDetected(f'Task {task!r} repeated the same prompt {budget.max_repeats} times')
        if budget.task_usd and self.spent_task(task) + estimate_usd > budget.task_usd:
            raise BudgetExceeded(f'Task budget ${budget.task_usd:.4f} would be exceeded')
        day_start = at.astimezone(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        if budget.daily_usd and self.spent_since(day_start) + estimate_usd > budget.daily_usd:
            raise BudgetExceeded(f'Daily budget ${budget.daily_usd:.4f} would be exceeded')

    def record(self, *, task: str, model: str, usage: dict, cost: float,
               saved_real: float, saved_est: float, at: dt.datetime, cache_hit: bool) -> None:
        self.conn.execute('''INSERT INTO usage (ts, task, model, prompt_tokens, cached_tokens,
            completion_tokens, cost_usd, saved_real_usd, saved_est_usd, tariff, cache_hit)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (
            at.timestamp(), task, model,
            int(usage.get('prompt_tokens') or 0), _cached_tokens(usage),
            int(usage.get('completion_tokens') or 0), cost, saved_real, saved_est,
            tariff(at), int(cache_hit)))
        self.conn.commit()

    def alerts(self, budget: Budget, at: dt.datetime) -> list[str]:
        messages = []
        day_start = at.astimezone(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        for label, spent, limit in (('daily', self.spent_since(day_start), budget.daily_usd),):
            if limit and spent / limit >= budget.alert_pct:
                messages.append(f'{label} spend ${spent:.4f} of ${limit:.4f}')
        return messages

    def report(self, days: int = 30, at: dt.datetime | None = None) -> dict:
        at = at or dt.datetime.now(dt.timezone.utc)
        since = at.timestamp() - days * 86400
        row = self.conn.execute('''SELECT COUNT(*), SUM(cache_hit), COALESCE(SUM(prompt_tokens),0),
            COALESCE(SUM(cached_tokens),0), COALESCE(SUM(completion_tokens),0),
            COALESCE(SUM(cost_usd),0), COALESCE(SUM(saved_real_usd),0),
            COALESCE(SUM(saved_est_usd),0) FROM usage WHERE ts>=?''', (since,)).fetchone()
        by_model = self.conn.execute('''SELECT model, COUNT(*), COALESCE(SUM(cost_usd),0)
            FROM usage WHERE ts>=? GROUP BY model ORDER BY 3 DESC''', (since,)).fetchall()
        return {
            'days': days, 'events': int(row[0] or 0), 'cache_hits': int(row[1] or 0),
            'prompt_tokens': int(row[2] or 0), 'cached_tokens': int(row[3] or 0),
            'completion_tokens': int(row[4] or 0), 'cost_usd': float(row[5] or 0),
            'saved_real_usd': float(row[6] or 0), 'saved_est_usd': float(row[7] or 0),
            'by_model': [{'model': r[0], 'events': int(r[1]), 'cost_usd': float(r[2])} for r in by_model],
        }
