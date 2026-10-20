from pathlib import Path

from deep_ultra.context import (ProjectIndex, build_pack, dependencies, entry_points,
                                render_markdown, detect_tests, _walk)


def make_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / 'package.json').write_text('{"dependencies":{"react":"18"},"devDependencies":{"vitest":"1"}}')
    (root / 'pyproject.toml').write_text('[project]\ndependencies = ["httpx>=0.27", "typer"]\n')
    (root / 'main.py').write_text('print("hi")\n')
    (root / 'src').mkdir()
    (root / 'src' / 'util.py').write_text('# TODO: validate input\nprint(1)\n')
    (root / 'tests').mkdir()
    (root / 'tests' / 'test_app.py').write_text('def test_ok():\n    assert True\n')
    (root / 'node_modules').mkdir()
    (root / 'node_modules' / 'junk.js').write_text('x' * 5000)


def test_pack_contains_structure_and_deps(tmp_path):
    make_project(tmp_path)
    pack = build_pack(tmp_path, 4000)
    assert pack['files'] == 5
    assert 'node' in pack['dependencies'] and 'react' in pack['dependencies']['node']
    assert 'python' in pack['dependencies'] and 'httpx' in pack['dependencies']['python']
    assert 'main.py' in pack['entry_points']
    assert pack['tests']['count'] == 1
    assert 'pytest' in pack['tests']['command']
    assert pack['todos']['count'] == 1
    assert pack['token_estimate'] <= 4000
    assert 'node_modules' not in ' '.join(pack['tree'])


def test_markdown_is_compact(tmp_path):
    make_project(tmp_path)
    text = render_markdown(build_pack(tmp_path, 2000))
    assert 'Project:' in text and 'main.py' in text and 'Tests:' in text


def test_project_index_is_incremental(tmp_path):
    make_project(tmp_path)
    index = ProjectIndex(tmp_path.parent / 'index.sqlite3')
    first = index.refresh(tmp_path)
    assert len(first['added']) == 5 and not first['changed']

    second = index.refresh(tmp_path)
    assert not second['added'] and not second['changed'] and not second['removed']

    target = tmp_path / 'main.py'
    target.write_text('print("changed")\n')
    third = index.refresh(tmp_path)
    assert third['changed'] == ['main.py']

    target.unlink()
    fourth = index.refresh(tmp_path)
    assert fourth['removed'] == ['main.py']
    assert index.total_tokens(tmp_path) > 0


def test_dependency_helpers(tmp_path):
    make_project(tmp_path)
    files = _walk(tmp_path)
    assert dependencies(tmp_path)['python'] == ['httpx', 'typer']
    assert entry_points(files) == ['main.py']
    assert detect_tests(files, dependencies(tmp_path))['command'].startswith('python -m pytest')
