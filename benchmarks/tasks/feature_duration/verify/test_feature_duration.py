import pytest
from duration import parse_duration


def test_values():
    assert parse_duration("1h30m") == 5400
    assert parse_duration("2d") == 172800
    assert parse_duration("1d2h3m4s") == 93784


@pytest.mark.parametrize("value", ["", "10", "1x", "-5m"])
def test_invalid(value):
    with pytest.raises(ValueError):
        parse_duration(value)
