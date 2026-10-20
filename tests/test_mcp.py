import json
from pathlib import Path

from deep_ultra.mcp import TOOLS, call_tool, handle


def test_initialize_and_tools(tmp_path):
    init = handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'}, tmp_path)
    assert init['result']['protocolVersion'] == '2024-11-05'
    listing = handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}, tmp_path)
    assert {tool['name'] for tool in listing['result']['tools']} == {
        'search_code', 'project_map', 'memory_search', 'memory_add'}
    assert len(TOOLS) == 4


def test_search_tool(tmp_path):
    (tmp_path / 'app.py').write_text('def parse_token(value):\n    return value\n')
    result = call_tool('search_code', {'query': 'parse token', 'budget_tokens': 200}, tmp_path)
    assert not result['isError'] and 'parse_token' in result['content'][0]['text']


def test_project_map(tmp_path):
    (tmp_path / 'main.py').write_text('print(1)\n')
    result = call_tool('project_map', {}, tmp_path)
    assert 'Project:' in result['content'][0]['text']


def test_memory_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    saved = call_tool('memory_add', {'kind': 'decision', 'key': 'cache', 'value': 'use ttl'}, tmp_path)
    assert saved['content'][0]['text'] == 'Saved'
    found = call_tool('memory_search', {'query': 'cache'}, tmp_path)
    assert 'use ttl' in found['content'][0]['text']


def test_unknown_tool(tmp_path):
    result = call_tool('nope', {}, tmp_path)
    assert result['isError']


def test_jsonrpc_error():
    response = handle({'jsonrpc': '2.0', 'id': 3, 'method': 'nope'}, Path('.'))
    assert response['error']['code'] == -32601
    assert json.dumps(response)
