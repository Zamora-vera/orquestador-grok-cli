import pytest
from security import read_file


def test_inside(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "ok.txt").write_text("ok")
    assert read_file(base, "ok.txt") == "ok"


def test_traversal(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (tmp_path / "secret.txt").write_text("secret")
    with pytest.raises(ValueError):
        read_file(base, "../secret.txt")
