"""Model router, persistent cache, safe coding-agent helpers.

No network call occurs unless explicitly requested by the CLI user.
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import httpx


@dataclass(frozen=True)
class Model:
    name: str
    provider: str
    vision: bool
    reasoning: bool
    cost_tier: int
    endpoint: str
    key_var: str


# Defaults are configurable; availability/capabilities can change by provider and account.
MODELS = (
    Model('deepseek-chat', 'deepseek', False, False, 1, 'https://api.deepseek.com', 'DEEPSEEK_API_KEY'),
    Model('deepseek-reasoner', 'deepseek', False, True, 2, 'https://api.deepseek.com', 'DEEPSEEK_API_KEY'),
    Model('kimi-k2.5', 'kimi', True, True, 3, 'https://api.moonshot.ai/v1', 'KIMI_API_KEY'),
)


def difficulty(prompt: str) -> int:
    """Transparent heuristic, not a trained quality predictor."""
    hard = ('architecture', 'arquitectura', 'refactor', 'security', 'seguridad',
            'concurrency', 'concurrencia', 'migration', 'migración', 'debug', 'depurar')
    score = int(len(prompt) > 600) + int(len(prompt) > 2500)
    score += sum(word in prompt.lower() for word in hard)
    return min(score, 3)


def choose_model(prompt: str, vision: bool = False, override: str | None = None,
                 models: tuple[Model, ...] = MODELS) -> Model:
    available = [m for m in models if os.getenv(m.key_var)]
    if override:
        selected = next((m for m in models if m.name == override), None)
        if not selected:
            raise ValueError(f'Unknown model: {override}')
        if not os.getenv(selected.key_var):
            raise ValueError(f'Missing {selected.key_var}')
        if vision and not selected.vision:
            raise ValueError('Model does not support image inputs')
        return selected
    available = [m for m in available if not vision or m.vision]
    if not available:
        raise ValueError('No suitable API key/model available')
    target = difficulty(prompt)
    return min(available, key=lambda m: (abs(int(m.reasoning) - int(target >= 1)) * 5 + m.cost_tier, m.cost_tier))


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
    def __init__(self, location: Path):
        location.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(location)
        self.conn.execute('CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, value TEXT NOT NULL, expires REAL NOT NULL)')
        self.conn.execute('CREATE TABLE IF NOT EXISTS memory (key TEXT PRIMARY KEY, value TEXT NOT NULL)')

    def get(self, key: str) -> str | None:
        row = self.conn.execute('SELECT value FROM entries WHERE key=? AND expires>?', (key, time.time())).fetchone()
        return row[0] if row else None

    def put(self, key: str, value: str, ttl: int = 3600) -> None:
        self.conn.execute('INSERT OR REPLACE INTO entries VALUES (?, ?, ?)', (key, value, time.time() + ttl))
        self.conn.commit()

    def remember(self, key: str, value: str) -> None:
        self.conn.execute('INSERT OR REPLACE INTO memory VALUES (?,?)', (key, value))
        self.conn.commit()

    def recall(self, key: str) -> str | None:
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


def cache_key(messages: list[dict[str, Any]], model: Model, temperature: float) -> str:
    raw = json.dumps({'messages': messages, 'model': model.name, 'provider': model.provider,
                      'temperature': temperature}, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def ask(prompt: str, cache: Cache, *, vision_path: Path | None = None,
        override: str | None = None, root: Path | None = None,
        ttl: int = 3600, temperature: float = 0) -> tuple[str, Model, bool]:
    selected = choose_model(prompt, vision_path is not None, override)
    content: Any = prompt
    if vision_path:
        content = [{'type': 'text', 'text': prompt}, file_image_data(vision_path)]
    context = ''
    if root:
        context = '\nRepository files (names only):\n' + '\n'.join(safe_repo_files(root))
    messages = [
        {'role': 'system', 'content': 'You are Deep Ultra, a precise software engineer. Never claim to have edited files, run tests, or deployed unless actually done. Ask before destructive operations.' + context},
        {'role': 'user', 'content': content},
    ]
    key = cache_key(messages, selected, temperature)
    cached = cache.get(key)
    if cached is not None:
        return cached, selected, True
    token = os.getenv(selected.key_var)
    if not token:
        raise ValueError(f'Missing {selected.key_var}')
    url = selected.endpoint.rstrip('/') + '/chat/completions'
    response = httpx.post(url, headers={'Authorization': f'Bearer {token}'},
                          json={'model': selected.name, 'messages': messages, 'temperature': temperature},
                          timeout=90)
    response.raise_for_status()
    result = response.json()['choices'][0]['message']['content']
    if not isinstance(result, str) or not result.strip():
        raise ValueError('Provider returned empty or unsupported content')
    cache.put(key, result, ttl)
    return result, selected, False


def run_tests(root: Path, command: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    """Only an explicit user-invoked command. Never executed autonomously."""
    cmd = command or ['python', '-m', 'pytest', '-q']
    allowed = (['python', '-m', 'pytest'], ['python3', '-m', 'pytest'], ['pytest'])
    if not any(cmd[:len(prefix)] == prefix for prefix in allowed):
        raise ValueError('Only pytest invocation is permitted')
    return subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=120, check=False)
