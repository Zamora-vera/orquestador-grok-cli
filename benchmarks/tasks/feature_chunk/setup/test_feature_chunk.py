import pytest
from lists import chunk


def test_chunks():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_invalid():
    with pytest.raises(ValueError):
        chunk([1], 0)
