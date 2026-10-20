"""Verifiable coding benchmarks run through Grok CLI and the local router.

Each task is a tiny project with a hidden verification suite. The agent works in
a temporary copy; verification files are restored before pytest runs, so editing
tests cannot fake a pass.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .core import Cache


@dataclass
class TaskResult:
    task: str
    difficulty: str
    model: str
    passed: bool
    seconds: float
    cost_usd: float
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    turns: int
    workspace: str
    detail: str


def _load(task_dir: Path) -> dict:
    meta = json.loads((task_dir / 'task.json').read_text(encoding='utf-8'))
    meta['prompt'] = (task_dir / 'prompt.txt').read_text(encoding='utf-8').strip()
    meta['dir'] = task_dir
    return meta


def _copy(source: Path, target: Path) -> None:
    for item in source.rglob('*'):
        if item.is_file():
            destination = target / item.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)


def _usage(cache: Cache) -> dict:
    report = cache.ledger.report(1)
    return {'cost': report['cost_usd'], 'prompt': report['prompt_tokens'],
            'cached': report['cached_tokens'], 'completion': report['completion_tokens'],
            'hits': report['cache_hits']}


def run_task(task_dir: Path, grok: str, max_turns: int, timeout: int,
             ledger: Cache) -> TaskResult:
    meta = _load(task_dir)
    workspace = Path(tempfile.mkdtemp(prefix=f"du-bench-{meta['id']}-"))
    _copy(task_dir / 'setup', workspace)
    before = _usage(ledger)
    started = time.time()
    command = [grok, '-p', meta['prompt'], '-m', meta['model'], '--yolo',
               '--max-turns', str(max_turns), '--no-auto-update', '--output-format', 'json',
               '--cwd', str(workspace)]
    turns = 0
    detail = ''
    env = os.environ.copy()
    if not env.get('DEEP_ULTRA_ROUTER_TOKEN'):
        try:
            from .token import get
            token = get()
        except Exception:  # noqa: BLE001 - CI has no credential backend
            token = None
        if token:
            env['DEEP_ULTRA_ROUTER_TOKEN'] = token
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=env)
        detail = (proc.stdout or '')[-1500:]
        try:
            payload = json.loads(proc.stdout)
            turns = int(payload.get('num_turns') or 0)
        except (json.JSONDecodeError, AttributeError):
            turns = 0
    except subprocess.TimeoutExpired:
        detail = 'timeout'
        proc = None
    seconds = time.time() - started
    _copy(task_dir / 'verify', workspace)
    check = subprocess.run([sys.executable, '-m', 'pytest', '-q'], cwd=workspace,
                           capture_output=True, text=True, timeout=180)
    passed = check.returncode == 0
    after = _usage(ledger)
    return TaskResult(
        task=meta['id'], difficulty=meta.get('difficulty', 'unknown'), model=meta['model'],
        passed=passed, seconds=round(seconds, 1),
        cost_usd=round(after['cost'] - before['cost'], 6),
        prompt_tokens=after['prompt'] - before['prompt'],
        cached_tokens=after['cached'] - before['cached'],
        completion_tokens=after['completion'] - before['completion'],
        turns=turns, workspace=str(workspace),
        detail=(detail or '') + '\n' + check.stdout[-800:],
    )


def run_bench(tasks_dir: Path, *, grok: str = 'grok', max_usd: float = 0.10,
              state_path: Path | None = None, out: Path | None = None,
              only: set[str] | None = None) -> dict:
    ledger = Cache(state_path or (Path.home() / '.cache' / 'deep-ultra' / 'state.sqlite3'))
    tasks = sorted(path for path in tasks_dir.iterdir()
                   if (path / 'task.json').exists() and (not only or path.name in only))
    results: list[TaskResult] = []
    spent = 0.0
    for task_dir in tasks:
        meta = _load(task_dir)
        if spent >= max_usd:
            break
        result = run_task(task_dir, grok, int(meta.get('max_turns', 6)),
                          int(meta.get('timeout', 300)), ledger)
        results.append(result)
        spent += max(result.cost_usd, 0)
    summary = {
        'tasks': [asdict(result) for result in results],
        'passed': sum(1 for result in results if result.passed),
        'total': len(results),
        'success_rate': round(sum(1 for result in results if result.passed) / len(results), 3) if results else 0,
        'cost_usd': round(sum(result.cost_usd for result in results), 6),
        'prompt_tokens': sum(result.prompt_tokens for result in results),
        'cached_tokens': sum(result.cached_tokens for result in results),
        'completion_tokens': sum(result.completion_tokens for result in results),
        'max_usd': max_usd,
    }
    if out:
        out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    return summary
