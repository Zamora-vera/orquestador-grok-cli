"""Router token storage backed by the OS credential store.

On Windows this is the Credential Locker through `keyring`; no plaintext token
is written to the repository, configuration or logs.
"""
from __future__ import annotations

import secrets

import keyring

SERVICE = 'deep-ultra'
USERNAME = 'router-token'


def get() -> str | None:
    return keyring.get_password(SERVICE, USERNAME)


def store(value: str) -> None:
    keyring.set_password(SERVICE, USERNAME, value)


def rotate() -> str:
    token = secrets.token_hex(32)
    store(token)
    return token


def delete() -> None:
    try:
        keyring.delete_password(SERVICE, USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass
