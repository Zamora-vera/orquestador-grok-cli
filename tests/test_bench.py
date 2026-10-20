from pathlib import Path

from deep_ultra.bench import _copy, _load, run_bench


def test_load_task(tmp_path):
    task = tmp_path / 't1'
    task.mkdir()
    (task / 'task.json').write_text('{"id":"t1","difficulty":"easy","model":"deep-ultra"}')
    (task / 'prompt.txt').write_text('fix it')
    meta = _load(task)
    assert meta['prompt'] == 'fix it' and meta['model'] == 'deep-ultra'


def test_copy_tree(tmp_path):
    source = tmp_path / 'src'
    source.mkdir()
    (source / 'a.py').write_text('x')
    target = tmp_path / 'dst'
    target.mkdir()
    _copy(source, target)
    assert (target / 'a.py').read_text() == 'x'


def test_empty_bench(tmp_path):
    summary = run_bench(tmp_path, max_usd=0.01, state_path=tmp_path / 'state.db')
    assert summary['total'] == 0
    assert summary['cost_usd'] == 0
