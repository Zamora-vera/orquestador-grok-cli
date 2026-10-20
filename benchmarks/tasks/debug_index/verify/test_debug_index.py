from utils import last


def test_last():
    assert last([1, 2, 3]) == 3
    assert last([]) is None
