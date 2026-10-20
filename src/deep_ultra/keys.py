"""Provider credentials: environment first, then the OS credential store."""
from __future__ import annotations

import os

import keyring

SERVICE = 'deep-ultra'


def provider_key(model) -> str | None:
    value = os.getenv(model.key_var)
    if value:
        return value
    try:
        value = keyring.get_password(SERVICE, model.key_var.lower().replace('_', '-'))
    except Exception:  # noqa: BLE001 - no backend or locked store
        value = None
    if value:
        os.environ[model.key_var] = value
    return value
