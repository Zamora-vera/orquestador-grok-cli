"""Incremental code retrieval: chunks, BM25-style search and bounded snippets.

Only text files up to 200 KB are indexed. The index stores content locally so a
query can return the few relevant lines instead of a whole repository.
"""
from __future__ import annotations

import hashlib
import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .context import TEXT_EXT, _walk
from .finops import estimate_tokens

CHUNK_LINES = 40
MAX_INDEX_BYTES = 200_000
TOKEN_RE = re.compile(r'[A-Za-z0-9]+')
STOP = {'the', 'and', 'for', 'with', 'from', 'this', 'that', 'def', 'class', 'import',
        'return', 'self', 'none', 'true', 'false', 'que', 'los', 'las', 'una', 'por',
        'para', 'con', 'del', 'como', 'más', 'mas'}


@dataclass(frozen=True)
class Chunk:
    path: str
    start: int
    end: int
    text: str


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)
            if len(token) > 2 and token.lower() not in STOP]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8', 'replace')).hexdigest()


class CodeIndex:
    def __init__(self, location: Path):
        location.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(location)
        self.conn.execute('''CREATE TABLE IF NOT EXISTS files (
            root TEXT NOT NULL, path TEXT NOT NULL, sha TEXT NOT NULL, tokens INTEGER NOT NULL,
            PRIMARY KEY (root, path))''')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS chunks (
            root TEXT NOT NULL, path TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
            text TEXT NOT NULL, PRIMARY KEY (root, path, start))''')
        self.conn.commit()

    def refresh(self, root: Path) -> dict:
        base = Path(root).resolve()
        key = str(base)
        old = {row[0]: row[1] for row in
               self.conn.execute('SELECT path, sha FROM files WHERE root=?', (key,))}
        seen = set()
        changed = 0
        for info in _walk(base):
            if info.ext not in TEXT_EXT or info.size > MAX_INDEX_BYTES:
                continue
            try:
                text = (base / info.path).read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            sha = _sha(text)
            seen.add(info.path)
            if old.get(info.path) == sha:
                continue
            changed += 1
            self.conn.execute('DELETE FROM chunks WHERE root=? AND path=?', (key, info.path))
            lines = text.splitlines()
            for start in range(0, len(lines), CHUNK_LINES):
                block = '\n'.join(lines[start:start + CHUNK_LINES])
                self.conn.execute('INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?)',
                                  (key, info.path, start + 1, min(start + CHUNK_LINES, len(lines)), block))
            self.conn.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?)',
                              (key, info.path, sha, estimate_tokens(text)))
        for path in set(old) - seen:
            self.conn.execute('DELETE FROM files WHERE root=? AND path=?', (key, path))
            self.conn.execute('DELETE FROM chunks WHERE root=? AND path=?', (key, path))
        self.conn.commit()
        return {'indexed': len(seen), 'changed': changed}

    def search(self, root: Path, query: str, limit: int = 6) -> list[tuple[float, Chunk]]:
        key = str(Path(root).resolve())
        terms = _tokens(query)
        rows = self.conn.execute('SELECT path, start, end, text FROM chunks WHERE root=?', (key,)).fetchall()
        if not rows or not terms:
            return []
        tokenized = [(_tokens(text), Chunk(path, start, end, text)) for path, start, end, text in rows]
        df: dict[str, int] = {}
        for tokens, _ in tokenized:
            for term in set(tokens) & set(terms):
                df[term] = df.get(term, 0) + 1
        total = len(tokenized)
        scored = []
        for tokens, chunk in tokenized:
            counts = {term: tokens.count(term) for term in terms}
            score = 0.0
            for term, count in counts.items():
                if not count:
                    continue
                idf = math.log(1 + (total - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
                score += (count * 2.2) / (count + 1.2) * idf
            if score:
                haystack = chunk.path.lower()
                score += 0.6 * sum(1 for term in terms if term in haystack)
                scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].path, item[1].start))
        return scored[:limit]

    def snippets(self, root: Path, query: str, budget_tokens: int = 1500,
                 limit: int = 6) -> str:
        """Best chunks within a token budget, deduplicated by path and range."""
        blocks = []
        used = 0
        for _, chunk in self.search(root, query, limit):
            header = f'{chunk.path}:{chunk.start}-{chunk.end}'
            lines = chunk.text.splitlines()
            while lines and used + estimate_tokens(header + '\n'.join(lines)) > budget_tokens:
                lines.pop()
            if not lines:
                continue
            block = f'--- {header}\n' + '\n'.join(lines)
            cost = estimate_tokens(block)
            if used + cost > budget_tokens:
                break
            blocks.append(block)
            used += cost
            if used >= budget_tokens * 0.9:
                break
        return '\n\n'.join(blocks)
