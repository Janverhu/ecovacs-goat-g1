"""Private persistence for the ECOVACS account session."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .mower_api import AccountSession
from .util import is_valid_session_store_id

STORAGE_VERSION = 1


class AccountSessionStoreError(RuntimeError):
    """The private account session could not be made durable."""


class AccountSessionStore:
    """Persist one account session in a mode-0600 Home Assistant store."""

    def __init__(
        self,
        hass: HomeAssistant,
        store_id: str,
        client_device_id: str,
        username: str,
        country: str,
    ) -> None:
        """Initialize a private, atomic store bound to one config entry."""
        self._store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}/auth_{store_id}",
            private=True,
            atomic_writes=True,
        )
        self._client_device_id = client_device_id
        self._account_fingerprint = _account_fingerprint(
            username, country, client_device_id
        )

    async def async_load(self) -> AccountSession | None:
        """Load a complete session, removing malformed or misbound data."""
        data = await self._store.async_load()
        if not isinstance(data, dict):
            if data is not None:
                await self._store.async_remove()
            return None
        if data.get("client_device_id") != self._client_device_id:
            await self._store.async_remove()
            return None
        if data.get("account_fingerprint") != self._account_fingerprint:
            await self._store.async_remove()
            return None
        user_id = data.get("user_id")
        access_token = data.get("access_token")
        if not isinstance(user_id, str) or not user_id:
            await self._store.async_remove()
            return None
        if not isinstance(access_token, str) or not access_token:
            await self._store.async_remove()
            return None
        return AccountSession(user_id=user_id, access_token=access_token)

    async def async_save(self, session: AccountSession | None) -> None:
        """Atomically save a session, or remove the store when it is invalid."""
        if session is None:
            await self._store.async_remove()
            return
        await self._store.async_save(
            {
                "client_device_id": self._client_device_id,
                "account_fingerprint": self._account_fingerprint,
                "user_id": session.user_id,
                "access_token": session.access_token,
            }
        )

    async def async_save_verified(self, session: AccountSession) -> None:
        """Save and read back a session across the one-time-code boundary."""
        await self.async_save(session)
        if await self.async_load() != session:
            raise AccountSessionStoreError(
                "ECOVACS account session was not written to private storage"
            )

    async def async_remove(self) -> None:
        """Remove the persisted account session."""
        await self._store.async_remove()


async def async_remove_account_session_store(
    hass: HomeAssistant, store_id: Any
) -> None:
    """Remove a private store by opaque id without requiring entry migration."""
    if not is_valid_session_store_id(store_id):
        return
    store = Store[dict[str, Any]](
        hass,
        STORAGE_VERSION,
        f"{DOMAIN}/auth_{store_id}",
        private=True,
        atomic_writes=True,
    )
    await store.async_remove()
    storage_dir = Path(hass.config.path(".storage", DOMAIN))
    await hass.async_add_executor_job(
        _remove_corrupt_store_siblings,
        storage_dir,
        f"auth_{store_id}.corrupt.",
    )


def _account_fingerprint(
    username: str, country: str, client_device_id: str
) -> str:
    """Return a non-reversible binding for the configured account identity."""
    material = "\0".join(
        (username.strip().casefold(), country.upper(), client_device_id)
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _remove_corrupt_store_siblings(
    storage_dir: Path, filename_prefix: str
) -> None:
    """Remove private corrupt-file remnants belonging to a deleted entry."""
    if not storage_dir.is_dir():
        return
    for candidate in storage_dir.iterdir():
        if candidate.is_file() and candidate.name.startswith(filename_prefix):
            candidate.unlink()
