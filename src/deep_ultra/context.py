"""Project analysis with incremental hashing and a bounded context pack.

The pack is what an agent sees about a repository: structure, dependencies,
entry points, tests and hotspots, trimmed to a token budget. File contents are
hashed for change detection and never sent anywhere.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .finops import estimate_tokens

IGNORED_DIRS = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', 'dist', 'build',
                '.next', 'target', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'coverage',
                '.idea', '.vscode', '.tox', '.eggs', 'vendor'}
TEXT_EXT = {'.py', '.ts', '.tsx', '.js', '.jsx', '.json', '.toml', '.yaml', '.yml', '.md',
            '.go', '.rs', '.java', '.rb', '.php', '.css', '.html', '.sql', '.sh', '.ps1',
            '.ini', '.cfg', '.txt', '.env'}
MAX_BYTES = 1_000_000
TREE_LIMIT = 120


@dataclass(frozen=True)
class FileInfo:
    path: str
    size: int
    mtime: float
    ext: str


def _walk(root: Path) -> list[FileInfo]:
    result: list[FileInfo] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not d.startswith('.'))
        for name in filenames:
            path = Path(dirpath) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size > MAX_BYTES:
                continue
            result.append(FileInfo(str(path.relative_to(root)).replace('\\', '/'),
                                   stat.st_size, stat.st_mtime, path.suffix.lower()))
    return sorted(result, key=lambda f: (-f.size, f.path))


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding='utf-8', errors='replace')[:limit]
    except OSError:
        return ''


def dependencies(root: Path) -> dict[str, list[str]]:
    deps: dict[str, list[str]] = {}

    def json_file(name: str, key: str) -> list[str]:
        path = root / name
        if not path.exists():
            return []
        try:
            data = json.loads(_read(path))
        except json.JSONDecodeError:
            return []
        block = data.get(key) or {}
        return sorted(block.keys()) if isinstance(block, dict) else []

    node = json_file('package.json', 'dependencies') + json_file('package.json', 'devDependencies')
    if node:
        deps['node'] = sorted(set(node))[:40]
    composer = json_file('composer.json', 'require')
    if composer:
        deps['php'] = composer[:40]

    pyproject = _read(root / 'pyproject.toml')
    python: list[str] = []
    if pyproject:
        match = re.search(r'dependencies\s*=\s*\[(.*?)\]', pyproject, re.S)
        if match:
            python += [re.split(r'[<>=!~; ]', name.strip().strip('"\''))[0]
                       for name in re.findall(r'["\']([^"\']+)["\']', match.group(1))]
    requirements = _read(root / 'requirements.txt')
    if requirements:
        python += [line.split('==')[0].split('>=')[0].strip()
                   for line in requirements.splitlines() if line.strip() and not line.startswith('#')]
    if python:
        deps['python'] = sorted(set(python))[:40]

    go_mod = _read(root / 'go.mod')
    if go_mod:
        deps['go'] = [line.split()[1] for line in go_mod.splitlines() if line.strip().startswith('require ')][:20]
    cargo = _read(root / 'Cargo.toml')
    if cargo:
        block = re.search(r'\[dependencies\](.*?)(\n\[|\Z)', cargo, re.S)
        if block:
            deps['rust'] = re.findall(r'^([A-Za-z0-9_-]+)\s*=', block.group(1), re.M)[:40]
    return deps


def entry_points(files: list[FileInfo]) -> list[str]:
    names = {'main.py', 'app.py', 'manage.py', 'server.py', 'src/main.ts', 'src/index.ts',
             'src/main.tsx', 'app/page.tsx', 'index.js', 'main.go', 'src/main.rs'}
    return [f.path for f in files if f.path in names]


def detect_tests(files: list[FileInfo], deps: dict[str, list[str]]) -> dict:
    test_files = [f.path for f in files if f.path.startswith('tests/') or f.path.startswith('test/')
                  or Path(f.path).name.startswith('test_') or '.test.' in f.path or '.spec.' in f.path]
    if 'pytest' in ' '.join(deps.get('python', [])) or any(p.endswith('.py') for p in test_files):
        command = 'python -m pytest -q'
    elif 'node' in deps or any('.test.' in p for p in test_files):
        command = 'npm test'
    elif 'go' in deps:
        command = 'go test ./...'
    elif 'rust' in deps:
        command = 'cargo test'
    else:
        command = ''
    return {'files': sorted(test_files)[:20], 'count': len(test_files), 'command': command}


def todos(root: Path, files: list[FileInfo]) -> dict:
    samples = []
    count = 0
    for info in files:
        if info.ext not in TEXT_EXT or info.size > 200_000:
            continue
        for line_no, line in enumerate(_read(root / info.path).splitlines(), 1):
            if 'TODO' in line or 'FIXME' in line:
                count += 1
                if len(samples) < 5:
                    samples.append(f'{info.path}:{line_no}: {line.strip()[:120]}')
    return {'count': count, 'samples': samples}


def git_summary(root: Path) -> dict:
    def run(*args: str) -> str:
        try:
            return subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                                  text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ''

    if not (root / '.git').exists():
        return {}
    status = run('status', '--porcelain')
    return {'branch': run('rev-parse', '--abbrev-ref', 'HEAD'),
            'last_commit': run('log', '-1', '--oneline'),
            'dirty_files': len([line for line in status.splitlines() if line.strip()])}


def _languages(files: list[FileInfo]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for info in files:
        if info.ext:
            counts[info.ext] = counts.get(info.ext, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1])[:8])


def build_pack(root: Path, budget_tokens: int = 6000, use_git: bool = True) -> dict:
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f'Not a directory: {root}')
    files = _walk(root)
    deps = dependencies(root)
    pack = {
        'root': str(root),
        'files': len(files),
        'languages': _languages(files),
        'tree': [info.path for info in files[:TREE_LIMIT]],
        'dependencies': deps,
        'entry_points': entry_points(files),
        'tests': detect_tests(files, deps),
        'todos': todos(root, files),
        'hotspots': [{'path': info.path, 'kb': round(info.size / 1024, 1)} for info in files[:5]],
    }
    if use_git:
        pack['git'] = git_summary(root)
    while estimate_tokens(json.dumps(pack)) > budget_tokens:
        if len(pack['tree']) > 10:
            pack['tree'] = pack['tree'][:max(10, len(pack['tree']) // 2)]
        elif pack['todos']['samples']:
            pack['todos']['samples'].pop()
        elif len(pack['hotspots']) > 2:
            pack['hotspots'].pop()
        else:
            break
    pack['token_estimate'] = estimate_tokens(json.dumps(pack))
    return pack


def render_markdown(pack: dict) -> str:
    lines = [f"Project: {pack['root']} ({pack['files']} files, ~{pack['token_estimate']} tokens)"]
    if pack.get('git'):
        git = pack['git']
        lines.append(f"Git: {git.get('branch', '?')} | dirty={git.get('dirty_files', 0)} | {git.get('last_commit', '')}")
    lines.append('Languages: ' + ', '.join(f'{ext}({count})' for ext, count in pack['languages'].items()))
    if pack['entry_points']:
        lines.append('Entry points: ' + ', '.join(pack['entry_points']))
    if pack['dependencies']:
        for ecosystem, names in pack['dependencies'].items():
            lines.append(f'Dependencies[{ecosystem}]: ' + ', '.join(names[:15]))
    if pack['tests']['count']:
        lines.append(f"Tests: {pack['tests']['count']} files; command: {pack['tests']['command'] or 'unknown'}")
    if pack['todos']['count']:
        lines.append(f"TODOs: {pack['todos']['count']}")
        lines.extend('  ' + sample for sample in pack['todos']['samples'])
    lines.append('Largest files: ' + ', '.join(f"{item['path']} ({item['kb']} KB)" for item in pack['hotspots']))
    lines.append('Tree:')
    lines.extend('  ' + path for path in pack['tree'])
    return '\n'.join(lines)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(131072), b''):
                digest.update(chunk)
    except OSError:
        return ''
    return digest.hexdigest()


class ProjectIndex:
    """Incremental file index: hashes only new or changed files."""

    def __init__(self, location: Path):
        location.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(location)
        self.conn.execute('''CREATE TABLE IF NOT EXISTS files (
            root TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL, mtime REAL NOT NULL,
            sha TEXT NOT NULL, tokens INTEGER NOT NULL, PRIMARY KEY (root, path))''')
        self.conn.commit()

    def refresh(self, root: Path, files: list[FileInfo] | None = None) -> dict:
        base = Path(root).resolve()
        key = str(base)
        files = files or _walk(base)
        old = {row[0]: (row[1], row[2], row[3]) for row in
               self.conn.execute('SELECT path, size, mtime, sha FROM files WHERE root=?', (key,))}
        added, changed = [], []
        seen = set()
        for info in files:
            seen.add(info.path)
            previous = old.get(info.path)
            sha = previous[2] if previous and previous[0] == info.size and previous[1] == info.mtime else _sha(base / info.path)
            if previous is None:
                added.append(info.path)
            elif previous[2] != sha:
                changed.append(info.path)
            self.conn.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)',
                              (key, info.path, info.size, info.mtime, sha, info.size // 4))
        removed = [path for path in old if path not in seen]
        for path in removed:
            self.conn.execute('DELETE FROM files WHERE root=? AND path=?', (key, path))
        self.conn.commit()
        return {'added': added, 'changed': changed, 'removed': removed, 'total': len(files)}

    def total_tokens(self, root: Path) -> int:
        row = self.conn.execute('SELECT COALESCE(SUM(tokens),0) FROM files WHERE root=?',
                                (str(Path(root).resolve()),)).fetchone()
        return int(row[0])


