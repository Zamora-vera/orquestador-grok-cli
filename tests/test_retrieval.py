from pathlib import Path

from deep_ultra.retrieval import CodeIndex


def make_repo(root: Path) -> None:
    (root / 'src').mkdir(parents=True)
    (root / 'src' / 'parser.py').write_text(
        'def parse_token(value):\n'
        '    """Parse an access token into parts."""\n'
        '    return value.split(".")\n\n'
        'def unrelated():\n'
        '    return 1\n')
    (root / 'src' / 'service.py').write_text(
        'from parser import parse_token\n\n'
        'def handle(request):\n'
        '    return parse_token(request.token)\n')


def test_index_and_search(tmp_path):
    make_repo(tmp_path)
    index = CodeIndex(tmp_path.parent / 'code.sqlite3')
    stats = index.refresh(tmp_path)
    assert stats['indexed'] == 2 and stats['changed'] == 2
    hits = index.search(tmp_path, 'parse access token')
    assert hits and hits[0][1].path == 'src/parser.py'
    assert 'def parse_token' in hits[0][1].text


def test_snippets_respect_budget(tmp_path):
    make_repo(tmp_path)
    index = CodeIndex(tmp_path.parent / 'code.sqlite3')
    index.refresh(tmp_path)
    text = index.snippets(tmp_path, 'parse token', budget_tokens=80)
    assert text.startswith('--- ')
    assert len(text) < 1200


def test_incremental_refresh(tmp_path):
    make_repo(tmp_path)
    index = CodeIndex(tmp_path.parent / 'code.sqlite3')
    index.refresh(tmp_path)
    second = index.refresh(tmp_path)
    assert second['changed'] == 0
    (tmp_path / 'src' / 'parser.py').write_text('def parse_token(value):\n    return value\n')
    third = index.refresh(tmp_path)
    assert third['changed'] == 1
