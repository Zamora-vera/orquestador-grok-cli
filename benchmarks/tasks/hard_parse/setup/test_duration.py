import pytest
from duration import parse_duration


def test_hours_minutes():
    assert parse_duration('1h30m') == 5400


def test_days():
    assert parse_duration('2d') == 172800


def test_seconds():
    assert parse_duration('45s') == 45


def test_all_units():
    assert parse_duration('1d2h3m4s') == 93784


@pytest.mark.parametrize('value', ['', '10', '1x', 'h', '-5m'])
def test_invalid(value):
    with pytest.raises(ValueError):
        parse_duration(value)
