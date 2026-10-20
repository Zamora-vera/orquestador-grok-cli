from parallel import parallel_sum


def test_sum():
    assert parallel_sum(list(range(200))) == sum(range(200))
