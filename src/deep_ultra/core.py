"""Model router, persistent cache and safe coding-agent helpers.

No network call occurs unless the CLI user explicitly runs ``chat``. Prices and
capabilities come from the dated catalog; nothing is hardcoded as permanent.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from . import finops
from .catalog import MODELS, ModelSpec, Price
from .finops import Budget, Ledger, PricingUnavailable, next_off_peak, tariff

DEFAULT_MAX_TOKENS = 4096
RETRY_DELAYS = (0.5, 1.0)
PROVIDER_RANK = {'deepseek': 0, 'kimi': 1}
BUDGET_MARGIN = 1.2


class DeferredWork(RuntimeError):
    """Deferrable work that would wait longer than allowed."""

    def __init__(self, message: str, wait_seconds: float):
        super().__init__(message)
        self.wait_seconds = wait_seconds


@dataclass(frozen=True)
class Answer:
    text: str
    model: ModelSpec
    cached: bool
    usage: dict[str, Any]
    cost_usd: float
    saved_real_usd: float
    saved_est_usd: float
    tariff: str


def difficulty(prompt: str) -> int:
    """Transparent heuristic, not a trained quality predictor."""
    hard = ('architecture', 'arquitectura', 'refactor', 'security', 'seguridad',
            'concurrency', 'concurrencia', 'migration', 'migración', 'debug', 'depurar')
    score = int(len(prompt) > 600) + int(len(prompt) > 2500)
    score += sum(word in prompt.lower() for word in hard)
    return min(score, 3)


def choose_model(prompt: str, vision: bool = False, context_tokens: int = 0,
                 override: str | None = None, models: tuple[ModelSpec, ...] = MODELS,
                 at: dt.datetime | None = None) -> ModelSpec:
    """Cheapest model that fits modality, context and task complexity."""
    at = at or dt.datetime.now(dt.timezone.utc)
    if override:
        selected = next((m for m in models if m.id == override), None)
        if not selected:
            raise ValueError(f'Unknown model: {override}')
        if not selected.available():
            raise ValueError(f'Missing {selected.key_var}')
        if vision and not selected.vision:
            raise ValueError('Model does not support image inputs')
        if selected.context < context_tokens:
            raise ValueError(f'{selected.id} context window is too small for this request')
        if not selected.pricing_verified:
            raise PricingUnavailable(f'No verified price for {selected.id}')
        return selected
    candidates = [m for m in models
                  if m.available() and m.pricing_verified
                  and (not vision or m.vision) and m.context >= context_tokens]
    if not candidates:
        raise ValueError('No suitable API key/model available')
    target_tier = 1 if difficulty(prompt) <= 1 else 2
    fitting = [m for m in candidates if m.tier >= target_tier] or candidates
    return min(fitting, key=lambda m: (PROVIDER_RANK.get(m.provider, 9), m.tier,
                                       (m.price(tariff(at)) or Price(0, 1e9, 1e9)).input))


def file_image_data(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 8_000_000:
        raise ValueError('Image too large (8 MB maximum)')
    types = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
    mime = types.get(path.suffix.lower())
    if not mime:
        raise ValueError('Unsupported image format')
    encoded = base64.b64encode(path.read_bytes()).decode('ascii')
    return {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{encoded}'}}


class Cache:
    """SQLite cache for provider answers plus a small persistent key/value store."""

    def __init__(self, location: Path):
        location.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(location, check_same_thread=False, timeout=30)
        self.conn.execute('CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, value TEXT NOT NULL, expires REAL NOT NULL)')
        self.conn.execute('CREATE TABLE IF NOT EXISTS memory (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        columns = {row[1] for row in self.conn.execute('PRAGMA table_info(entries)')}
        if 'cost_usd' not in columns:
            self.conn.execute('ALTER TABLE entries ADD COLUMN cost_usd REAL NOT NULL DEFAULT 0')
        if 'usage' not in columns:
            self.conn.execute('ALTER TABLE entries ADD COLUMN usage TEXT NOT NULL DEFAULT \'{}\'')
        self.conn.commit()
        self.ledger = Ledger(self.conn, self.lock)

    def get(self, key: str) -> str | None:
        entry = self.get_entry(key)
        return entry[0] if entry else None

    def get_entry(self, key: str) -> tuple[str, float, dict] | None:
        with self.lock:
            row = self.conn.execute('SELECT value, cost_usd, usage FROM entries WHERE key=? AND expires>?',
                                    (key, time.time())).fetchone()
        if not row:
            return None
        try:
            usage = json.loads(row[2] or '{}')
        except json.JSONDecodeError:
            usage = {}
        return row[0], float(row[1] or 0), usage

    def put(self, key: str, value: str, ttl: int = 3600, cost_usd: float = 0,
            usage: dict | None = None) -> None:
        with self.lock:
            self.conn.execute('INSERT OR REPLACE INTO entries (key, value, expires, cost_usd, usage) VALUES (?, ?, ?, ?, ?)',
                              (key, value, time.time() + ttl, cost_usd, json.dumps(usage or {})))
            self.conn.commit()

    def purge_expired(self) -> int:
        with self.lock:
            cur = self.conn.execute('DELETE FROM entries WHERE expires<=?', (time.time(),))
            self.conn.commit()
        return cur.rowcount

    def remember(self, key: str, value: str) -> None:
        with self.lock:
            self.conn.execute('INSERT OR REPLACE INTO memory VALUES (?,?)', (key, value))
            self.conn.commit()

    def recall(self, key: str) -> str | None:
        with self.lock:
            row = self.conn.execute('SELECT value FROM memory WHERE key=?', (key,)).fetchone()
        return row[0] if row else None


def safe_repo_files(root: Path, limit: int = 100) -> list[str]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError('Repository directory does not exist')
    ignored = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.deep-ultra'}
    result: list[str] = []
    for p in sorted(root.rglob('*')):
        if len(result) >= limit:
            break
        if p.is_symlink() or not p.is_file() or any(x in ignored for x in p.relative_to(root).parts):
            continue
        result.append(str(p.relative_to(root)))
    return result


def cache_key(messages: list[dict[str, Any]], model: ModelSpec, temperature: float,
              max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    raw = json.dumps({'messages': messages, 'model': model.id, 'provider': model.provider,
                      'temperature': temperature, 'max_tokens': max_tokens},
                     sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def build_messages(prompt: str, root: Path | None, vision_path: Path | None) -> list[dict[str, Any]]:
    content: Any = prompt
    if vision_path:
        content = [{'type': 'text', 'text': prompt}, file_image_data(vision_path)]
    context = ''
    if root:
        context = '\nRepository files (names only):\n' + '\n'.join(safe_repo_files(root))
    return [
        {'role': 'system', 'content': 'You are Deep Ultra, a precise software engineer. Never claim to have edited files, run tests, or deployed unless actually done. Ask before destructive operations.' + context},
        {'role': 'user', 'content': content},
    ]


def _context_tokens(messages: list[dict[str, Any]]) -> int:
    """Text estimate that ignores base64 image bodies and budgets one image tile."""
    raw = re.sub(r'data:image/[^"\']+', '[image]', json.dumps(messages, ensure_ascii=False))
    images = sum(1 for message in messages if isinstance(message.get('content'), list))
    return finops.estimate_tokens(raw) + 1500 * images


def _post(model: ModelSpec, messages: list[dict[str, Any]], temperature: float,
          max_tokens: int, timeout: float) -> tuple[str, dict[str, Any]]:
    token = os.getenv(model.key_var)
    if not token:
        raise ValueError(f'Missing {model.key_var}')
    url = model.endpoint.rstrip('/') + '/chat/completions'
    response = httpx.post(url, headers={'Authorization': f'Bearer {token}'},
                          json={'model': model.id, 'messages': messages,
                                'temperature': temperature, 'max_tokens': max_tokens},
                          timeout=timeout)
    response.raise_for_status()
    data = response.json()
    message = data['choices'][0]['message']
    text = message['content']
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Provider returned empty or unsupported content')
    return text, data.get('usage') or {}


def _fallback_models(selected: ModelSpec, vision: bool, context_tokens: int,
                     models: tuple[ModelSpec, ...], at: dt.datetime) -> list[ModelSpec]:
    """Same-provider fallbacks only: never ship a prompt to another vendor silently."""
    siblings = [m for m in models
                if m.provider == selected.provider and m.id != selected.id
                and m.available() and m.pricing_verified
                and (not vision or m.vision) and m.context >= context_tokens]

    def price_of(model: ModelSpec) -> float:
        price = model.price(tariff(at))
        return price.input if price else 1e9

    return [selected] + sorted(siblings, key=lambda m: (m.tier, price_of(m)))


def ask(prompt: str, cache: Cache, *, vision_path: Path | None = None,
        override: str | None = None, root: Path | None = None,
        ttl: int = 3600, temperature: float = 0,
        max_tokens: int | None = None, task: str = 'default',
        budget: Budget | None = None, defer: bool = False, max_wait: float = 0.0,
        now: dt.datetime | None = None,
        models: tuple[ModelSpec, ...] = MODELS) -> Answer:
    """Answer a prompt under budget, cache and loop protection."""
    limit = max_tokens or int(os.getenv('DEEP_ULTRA_MAX_TOKENS', DEFAULT_MAX_TOKENS))
    at = now or dt.datetime.now(dt.timezone.utc)
    budget = budget or Budget.from_env()
    messages = build_messages(prompt, root, vision_path)
    context_tokens = _context_tokens(messages)
    selected = choose_model(prompt, vision_path is not None, context_tokens, override, models, at)

    if defer and finops.is_peak(at):
        wait = (next_off_peak(at) - at).total_seconds()
        if wait > max_wait:
            raise DeferredWork(f'Off-peak starts in {wait / 60:.1f} min; max wait is {max_wait / 60:.1f} min',
                               wait)
        time.sleep(wait)
        at = at + dt.timedelta(seconds=wait)

    last_error: Exception | None = None
    for model in _fallback_models(selected, vision_path is not None, context_tokens, models, at):
        key = cache_key(messages, model, temperature, limit)
        entry = cache.get_entry(key)
        if entry is not None:
            text, stored_cost, _ = entry
            cache.ledger.record(task=task, model=model.id, usage={}, cost=0,
                                saved_real=stored_cost, saved_est=0, at=at, cache_hit=True)
            return Answer(text, model, True, {}, 0.0, stored_cost, 0.0, tariff(at))

        estimate = finops.estimate_cost(model, context_tokens, limit, at) * BUDGET_MARGIN
        cache.ledger.check(task, prompt, estimate, budget, at)
        cache.ledger.bump_attempt(task, Ledger.hash_prompt(prompt))

        for delay in (0, *RETRY_DELAYS):
            if delay:
                time.sleep(delay)
            try:
                text, usage = _post(model, messages, temperature, limit, timeout=90)
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code
                # 4xx other than rate limiting will not improve on retry.
                if status != 429 and status < 500:
                    break
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
            else:
                cost, cache_saving, offpeak_saving = finops.actual_cost(model, usage, at)
                cache.put(key, text, ttl, cost_usd=cost, usage=usage)
                cache.ledger.record(task=task, model=model.id, usage=usage, cost=cost,
                                    saved_real=0, saved_est=cache_saving + offpeak_saving,
                                    at=at, cache_hit=False)
                return Answer(text, model, False, usage, cost, 0.0,
                              cache_saving + offpeak_saving, tariff(at))
    raise RuntimeError(f'Provider request failed: {last_error}')


def run_tests(root: Path, command: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    """Only an explicit user-invoked command. Never executed autonomously."""
    cmd = command or ['python', '-m', 'pytest', '-q']
    allowed = (['python', '-m', 'pytest'], ['python3', '-m', 'pytest'], ['pytest'])
    if not any(cmd[:len(prefix)] == prefix for prefix in allowed):
        raise ValueError('Only pytest invocation is permitted')
    return subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=120, check=False)
