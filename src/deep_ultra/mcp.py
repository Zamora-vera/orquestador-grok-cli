"""Minimal MCP stdio server exposing retrieval, project analysis and memory.

Tools are read-only except `memory_add`, which writes to the local SQLite memory.
No shell command is executed and every path stays under the requested repo.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .context import build_pack, render_markdown
from .memory import Memory
from .retrieval import CodeIndex

PROTOCOL = '2024-11-05'
TOOLS = [
    {
        'name': 'search_code',
        'description': 'Search the repository index and return the most relevant code snippets.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'query': {'type': 'string'},
                'budget_tokens': {'type': 'integer', 'default': 1200},
                'limit': {'type': 'integer', 'default': 6},
            },
            'required': ['query'],
        },
    },
    {
        'name': 'project_map',
        'description': 'Summarise structure, dependencies, entry points, tests and hotspots.',
        'inputSchema': {
            'type': 'object',
            'properties': {'budget_tokens': {'type': 'integer', 'default': 2000}},
        },
    },
    {
        'name': 'memory_search',
        'description': 'Search local programming memory (decisions, resolved errors, patterns).',
        'inputSchema': {
            'type': 'object',
            'properties': {'query': {'type': 'string'},
                           'limit': {'type': 'integer', 'default': 5}},
            'required': ['query'],
        },
    },
    {
        'name': 'memory_add',
        'description': 'Store a decision, resolved error or reusable pattern in local memory.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'kind': {'type': 'string'},
                'key': {'type': 'string'},
                'value': {'type': 'string'},
                'ttl_days': {'type': 'integer', 'default': 90},
            },
            'required': ['kind', 'key', 'value'],
        },
    },
]


def _cache_dir() -> Path:
    return Path.home() / '.cache' / 'deep-ultra'


def _text(content: str) -> dict:
    return {'content': [{'type': 'text', 'text': content}], 'isError': False}


def _error(message: str) -> dict:
    return {'content': [{'type': 'text', 'text': message}], 'isError': True}


def call_tool(name: str, args: dict, repo: Path) -> dict:
    try:
        if name == 'search_code':
            index = CodeIndex(_cache_dir() / 'code.sqlite3')
            index.refresh(repo)
            text = index.snippets(repo, str(args['query']),
                                  int(args.get('budget_tokens') or 1200),
                                  int(args.get('limit') or 6))
            return _text(text or 'No matches')
        if name == 'project_map':
            return _text(render_markdown(build_pack(repo, int(args.get('budget_tokens') or 2000))))
        if name == 'memory_search':
            items = Memory(_cache_dir() / 'memory.sqlite3').search(
                str(args['query']), int(args.get('limit') or 5))
            return _text('\n'.join(f"[{i['kind']}] {i['key']}: {i['value']}" for i in items)
                         or 'No memories')
        if name == 'memory_add':
            Memory(_cache_dir() / 'memory.sqlite3').add(
                str(args['kind']), str(args['key']), str(args['value']),
                int(args.get('ttl_days') or 90))
            return _text('Saved')
    except KeyError as exc:
        return _error(f'Missing argument: {exc}')
    except Exception as exc:  # noqa: BLE001 - report to the client instead of crashing
        return _error(str(exc))
    return _error(f'Unknown tool: {name}')


def handle(request: dict, repo: Path) -> dict | None:
    method = request.get('method')
    request_id = request.get('id')
    if method == 'initialize':
        return {'jsonrpc': '2.0', 'id': request_id, 'result': {
            'protocolVersion': PROTOCOL, 'capabilities': {'tools': {}},
            'serverInfo': {'name': 'deep-ultra', 'version': '0.7.0'}}}
    if method == 'tools/list':
        return {'jsonrpc': '2.0', 'id': request_id, 'result': {'tools': TOOLS}}
    if method == 'tools/call':
        params = request.get('params') or {}
        result = call_tool(params.get('name', ''), params.get('arguments') or {}, repo)
        return {'jsonrpc': '2.0', 'id': request_id, 'result': result}
    if method in ('notifications/initialized', 'notifications/cancelled'):
        return None
    if request_id is None:
        return None
    return {'jsonrpc': '2.0', 'id': request_id,
            'error': {'code': -32601, 'message': f'Method not found: {method}'}}


def serve(repo: Path) -> None:
    root = Path(repo).resolve()
    if not root.is_dir():
        raise SystemExit(f'Not a directory: {root}')
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps({'jsonrpc': '2.0', 'id': None,
                              'error': {'code': -32700, 'message': 'Parse error'}}), flush=True)
            continue
        response = handle(request, root)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
