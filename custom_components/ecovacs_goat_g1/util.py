"""Ecovacs util functions."""

from __future__ import annotations

from collections.abc import Mapping
import secrets
import string
from typing import Any

from .const import CONF_DEVICE_ID, CONF_SESSION_STORE_ID

CLIENT_DEVICE_ID_LENGTH = 8
CLIENT_DEVICE_ID_ALPHABET = string.ascii_uppercase + string.digits
SESSION_STORE_ID_LENGTH = 32
SESSION_STORE_ID_ALPHABET = string.hexdigits.lower()[:16]


def generate_client_device_id() -> str:
    """Generate the exact eight-character id expected by ECOVACS clients."""
    return "".join(
        secrets.choice(CLIENT_DEVICE_ID_ALPHABET)
        for _ in range(CLIENT_DEVICE_ID_LENGTH)
    )


def generate_session_store_id() -> str:
    """Generate an opaque id for the private account-session store."""
    return secrets.token_hex(SESSION_STORE_ID_LENGTH // 2)


def is_valid_session_store_id(store_id: Any) -> bool:
    """Return whether a private-store id is safe to use in a storage key."""
    return (
        isinstance(store_id, str)
        and len(store_id) == SESSION_STORE_ID_LENGTH
        and all(character in SESSION_STORE_ID_ALPHABET for character in store_id)
    )


def is_valid_client_device_id(device_id: Any) -> bool:
    """Return whether a value matches ECOVACS' client-device id format."""
    return (
        isinstance(device_id, str)
        and len(device_id) == CLIENT_DEVICE_ID_LENGTH
        and all(character in CLIENT_DEVICE_ID_ALPHABET for character in device_id)
    )


def migrate_client_device_id(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return config data with one stable ECOVACS-compatible device id."""
    data = dict(config)
    if not is_valid_client_device_id(data.get(CONF_DEVICE_ID)):
        data[CONF_DEVICE_ID] = generate_client_device_id()
    return data


def migrate_config_data(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return config data with stable protocol and private-store identifiers."""
    data = migrate_client_device_id(config)
    if not is_valid_session_store_id(data.get(CONF_SESSION_STORE_ID)):
        data[CONF_SESSION_STORE_ID] = generate_session_store_id()
    return data


def get_client_device_id(config: Mapping[str, Any]) -> str:
    """Return the persisted ECOVACS-compatible client id."""
    device_id = config.get(CONF_DEVICE_ID)
    if is_valid_client_device_id(device_id):
        return device_id
    raise ValueError("ECOVACS client device id is missing or invalid")


def get_session_store_id(config: Mapping[str, Any]) -> str:
    """Return the validated opaque id for private account-session storage."""
    store_id = config.get(CONF_SESSION_STORE_ID)
    if is_valid_session_store_id(store_id):
        return store_id
    raise ValueError("ECOVACS private session store id is missing or invalid")
