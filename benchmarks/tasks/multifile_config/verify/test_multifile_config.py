from app import port


def test_port():
    assert port() == 8080
