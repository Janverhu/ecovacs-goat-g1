"""Controller for the mower-only ECOVACS integration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
import logging
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import aiohttp_client, device_registry as dr

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_UID,
    CONF_SESSION_STORE_ID,
    DEFAULT_DEBUG_CAPTURE_MAX_DURATION_MINUTES,
    DEFAULT_DEBUG_CAPTURE_MAX_SIZE_MB,
    DEFAULT_DEBUG_CAPTURE_RAW_PAYLOADS,
    DOMAIN,
    OPTION_DEBUG_CAPTURE_MAX_DURATION_MINUTES,
    OPTION_DEBUG_CAPTURE_MAX_SIZE_MB,
    OPTION_DEBUG_CAPTURE_RAW_PAYLOADS,
)
from .debug_capture import DebugCaptureStore
from .mower_api import (
    AccountSession,
    DeviceVerificationRequiredError,
    EcovacsAuthError,
    EcovacsMowerApi,
)
from .mower_coordinator import MowerCoordinator
from .mower_models import MowerDevice
from .session_store import AccountSessionStore
from .util import get_client_device_id, get_session_store_id

_LOGGER = logging.getLogger(__name__)


class EcovacsController:
    """Mower-only ECOVACS controller."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize controller."""
        self._hass = hass
        self._entry = entry
        config: Mapping[str, Any] = entry.data
        self._config = config
        self._configured_name = str(config.get(CONF_NAME) or "Ecovacs-GOAT")
        self._debug_capture = DebugCaptureStore(
            Path(hass.config.path("ecovacs_goat_g1_debug")),
            Path(hass.config.path("www", "ecovacs_goat", "debug")),
        )
        self._configure_debug_capture(entry.options)
        self._device_id = get_client_device_id(config)
        self._store_id = get_session_store_id(config)
        self._session_store = AccountSessionStore(
            hass,
            self._store_id,
            self._device_id,
            str(config[CONF_USERNAME]),
            str(config[CONF_COUNTRY]),
        )
        for value in (
            config.get(CONF_USERNAME),
            config.get(CONF_PASSWORD),
            self._configured_name,
            self._device_id,
        ):
            self._debug_capture.add_redaction_value(value)
        self._api: EcovacsMowerApi | None = None
        self._coordinators: list[MowerCoordinator] = []
        self._accept_session_updates = True
        self._session_write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize mower devices and coordinators."""
        started: list[MowerCoordinator] = []
        try:
            legacy_session = self._legacy_session_from_entry()
            account_session = await self._session_store.async_load()
            if account_session is None:
                account_session = legacy_session
            if account_session is not None:
                self._add_session_redactions(account_session)
            if legacy_session is not None and account_session is legacy_session:
                await self._session_store.async_save(legacy_session)
            self._persist_identifiers()
            api = EcovacsMowerApi(
                aiohttp_client.async_get_clientsession(self._hass),
                username=self._config[CONF_USERNAME],
                password=self._config[CONF_PASSWORD],
                country=self._config[CONF_COUNTRY],
                device_id=self._device_id,
                account_session=account_session,
                account_session_update_callback=self._async_account_session_updated,
                debug_capture=self._debug_capture,
            )
            self._api = api
            await api.authenticate()
            devices = await api.get_devices()
            self._remove_unbound_devices(devices)
            if not devices:
                raise ConfigEntryNotReady("No ECOVACS mower devices found")

            for device in devices:
                device = replace(device, name=self._configured_name)
                for value in (
                    device.did,
                    device.device_class,
                    device.resource,
                    device.name,
                    device.model,
                ):
                    self._debug_capture.add_redaction_value(value)
                coordinator = MowerCoordinator(
                    self._hass,
                    api,
                    device,
                    self._debug_capture,
                )
                await coordinator.async_start()
                started.append(coordinator)
                _LOGGER.info("Initialized ECOVACS mower %s", device.name)
            self._coordinators = started
        except DeviceVerificationRequiredError as ex:
            raise ConfigEntryAuthFailed(
                "ECOVACS device verification required"
            ) from ex
        except EcovacsAuthError as ex:
            raise ConfigEntryAuthFailed("Invalid ECOVACS credentials") from ex
        except ConfigEntryNotReady:
            await self._stop_coordinators(started)
            raise
        except Exception as ex:
            await self._stop_coordinators(started)
            raise ConfigEntryNotReady("Error during ECOVACS mower setup") from ex

    async def teardown(self) -> None:
        """Disconnect controller."""
        self._accept_session_updates = False
        async with self._session_write_lock:
            pass
        await self._stop_coordinators(self._coordinators)
        self._coordinators.clear()

    async def _async_account_session_updated(
        self, account_session: AccountSession | None
    ) -> None:
        """Persist session rotations and definitive invalidation privately."""
        async with self._session_write_lock:
            if not self._accept_session_updates:
                return
            if account_session is not None:
                self._add_session_redactions(account_session)
            await self._session_store.async_save(account_session)

    def _add_session_redactions(self, account_session: AccountSession) -> None:
        """Ensure raw account credentials never appear in debug captures."""
        self._debug_capture.add_redaction_value(account_session.user_id)
        self._debug_capture.add_redaction_value(account_session.access_token)

    def _legacy_session_from_entry(self) -> AccountSession | None:
        """Move tokens written by 1.0.0b1 out of the config entry."""
        user_id = self._config.get(CONF_ACCOUNT_UID)
        access_token = self._config.get(CONF_ACCESS_TOKEN)
        if not user_id or not access_token:
            return None
        return AccountSession(user_id=str(user_id), access_token=str(access_token))

    def _persist_identifiers(self) -> None:
        """Keep device and store ids on the entry; drop leftover tokens."""
        new_data = {
            key: value
            for key, value in dict(self._entry.data).items()
            if key not in {CONF_ACCOUNT_UID, CONF_ACCESS_TOKEN}
        }
        new_data[CONF_DEVICE_ID] = self._device_id
        new_data[CONF_SESSION_STORE_ID] = self._store_id
        if new_data != dict(self._entry.data):
            self._hass.config_entries.async_update_entry(self._entry, data=new_data)
            self._config = new_data

    def _remove_unbound_devices(self, devices: list[MowerDevice]) -> None:
        """Drop registry devices whose robot is no longer bound to the account."""
        device_registry = dr.async_get(self._hass)
        bound = {device.did for device in devices}
        for device_entry in dr.async_entries_for_config_entry(
            device_registry, self._entry.entry_id
        ):
            dids = {
                identifier[1]
                for identifier in device_entry.identifiers
                if identifier[0] == DOMAIN
            }
            if not dids or dids <= bound:
                continue
            _LOGGER.info(
                "Removing ECOVACS device no longer on the account: %s",
                ", ".join(sorted(dids - bound)),
            )
            device_registry.async_update_device(
                device_entry.id,
                remove_config_entry_id=self._entry.entry_id,
            )

    async def _stop_coordinators(
        self, coordinators: list[MowerCoordinator]
    ) -> None:
        """Stop any coordinators that were already started."""
        for coordinator in coordinators:
            await coordinator.async_stop()

    @property
    def coordinators(self) -> list[MowerCoordinator]:
        """Return mower coordinators."""
        return self._coordinators

    @property
    def devices(self) -> list[dict[str, Any]]:
        """Return raw device info for diagnostics."""
        return [coordinator.device.raw for coordinator in self._coordinators]

    @property
    def debug_capture(self) -> DebugCaptureStore:
        """Return debug capture store."""
        return self._debug_capture

    def _configure_debug_capture(self, options: Mapping[str, Any]) -> None:
        """Apply capture defaults from config entry options."""
        self._debug_capture.configure(
            include_raw_payloads=bool(
                options.get(
                    OPTION_DEBUG_CAPTURE_RAW_PAYLOADS,
                    DEFAULT_DEBUG_CAPTURE_RAW_PAYLOADS,
                )
            ),
            max_duration_seconds=int(
                options.get(
                    OPTION_DEBUG_CAPTURE_MAX_DURATION_MINUTES,
                    DEFAULT_DEBUG_CAPTURE_MAX_DURATION_MINUTES,
                )
            )
            * 60,
            max_bytes=int(
                options.get(
                    OPTION_DEBUG_CAPTURE_MAX_SIZE_MB,
                    DEFAULT_DEBUG_CAPTURE_MAX_SIZE_MB,
                )
            )
            * 1024
            * 1024,
        )
