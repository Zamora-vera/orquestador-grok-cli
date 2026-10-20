from security_secret import public_config


def test_redaction():
    result = public_config({"user": "a", "password": "x", "token": "y", "secret": "z"})
    assert result == {"user": "a"}
