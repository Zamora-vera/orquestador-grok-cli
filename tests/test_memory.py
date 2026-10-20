from pathlib import Path

from deep_ultra.memory import Memory


def test_add_and_search(tmp_path):
    memory = Memory(tmp_path / 'memory.sqlite3')
    memory.add('decision', 'routing', 'Flash by default; Pro after a failed test')
    memory.add('error', 'winerror 10053', 'ignore client disconnects')
    results = memory.search('failed test routing')
    assert results and results[0]['key'] == 'routing'


def test_expiry(tmp_path):
    memory = Memory(tmp_path / 'memory.sqlite3')
    memory.add('decision', 'old', 'stale guidance', ttl_days=-1)
    assert memory.search('stale') == []
    assert memory.prune() == 1


def test_context_is_bounded(tmp_path):
    memory = Memory(tmp_path / 'memory.sqlite3')
    for index in range(10):
        memory.add('pattern', f'p{index}', 'x' * 400, ttl_days=10)
    text = memory.context('pattern', limit=10, max_chars=500)
    assert 0 < len(text) <= 500
