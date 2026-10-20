"""Local programming memory: decisions, resolved errors and useful patterns.

Entries expire, so stale guidance stops being injected automatically.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

TOKEN_RE = re.compile(r'[A-Za-z0-9_]+')


class Memory:
    def __init__(self, location: Path):
        location.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(location)
        self.conn.execute('''CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, key TEXT NOT NULL,
            value TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
            uses INTEGER NOT NULL DEFAULT 0, UNIQUE(kind, key))''')
        self.conn.commit()

    def add(self, kind: str, key: str, value: str, ttl_days: int = 90) -> None:
        now = time.time()
        self.conn.execute('''INSERT INTO memories (kind, key, value, created, expires)
            VALUES (?,?,?,?,?)
            ON CONFLICT(kind, key) DO UPDATE SET value=excluded.value,
            created=excluded.created, expires=excluded.expires''',
                          (kind, key, value, now, now + ttl_days * 86400))
        self.conn.commit()

    def search(self, query: str, limit: int = 5) -> list[dict]:
        terms = {token.lower() for token in TOKEN_RE.findall(query) if len(token) > 2}
        rows = self.conn.execute('''SELECT id, kind, key, value, expires FROM memories
            WHERE expires>? ORDER BY created DESC''', (time.time(),)).fetchall()
        scored = []
        for row in rows:
            haystack = f'{row[1]} {row[2]} {row[3]}'.lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, row))
        scored.sort(key=lambda item: (-item[0], -item[1][4]))
        return [{'id': row[0], 'kind': row[1], 'key': row[2], 'value': row[3]}
                for _, row in scored[:limit]]

    def list(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute('''SELECT id, kind, key, value FROM memories
            WHERE expires>? ORDER BY created DESC LIMIT ?''', (time.time(), limit)).fetchall()
        return [{'id': row[0], 'kind': row[1], 'key': row[2], 'value': row[3]} for row in rows]

    def prune(self) -> int:
        cur = self.conn.execute('DELETE FROM memories WHERE expires<=?', (time.time(),))
        self.conn.commit()
        return cur.rowcount

    def context(self, query: str, limit: int = 5, max_chars: int = 1200) -> str:
        items = self.search(query, limit)
        if not items:
            return ''
        lines = [f"- [{item['kind']}] {item['key']}: {item['value']}" for item in items]
        text = '\n'.join(lines)
        return text[:max_chars]
