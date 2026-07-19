"""Controller for the mower-only ECOVACS integration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
import logging
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_COUNTRY, CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import aiohttp_client

from .const import (
    DEFAULT_DEBUG_CAPTURE_MAX_DURATION_MINUTES,
    DEFAULT_DEBUG_CAPTURE_MAX_SIZE_MB,
    DEFAULT_DEBUG_CAPTURE_RAW_PAYLOADS,
    OPTION_DEBUG_CAPTURE_MAX_DURATION_MINUTES,
    OPTION_DEBUG_CAPTURE_MAX_SIZE_MB,
    OPTION_DEBUG_CAPTURE_RAW_PAYLOADS,
)
from .debug_capture import DebugCaptureStore
from .mower_api import (
    AccountSession,
    EcovacsAuthError,
    EcovacsDeviceVerificationRequiredError,
    EcovacsInvalidAuthError,
    EcovacsMowerApi,
)
from .mower_coordinator import MowerCoordinator
from .session_store import AccountSessionStore
from .util import get_client_device_id, get_session_store_id

_LOGGER = logging.getLogger(__name__)


class EcovacsController:
    """Mower-only ECOVACS controller."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize controller."""
        self._hass = hass
        config: Mapping[str, Any] = entry.data
        self._config = config
        self._configured_name = str(config.get(CONF_NAME) or "Ecovacs-GOAT")
        self._debug_capture = DebugCaptureStore(
            Path(hass.config.path("ecovacs_goat_g1_debug")),
            Path(hass.config.path("www", "ecovacs_goat", "debug")),
        )
        self._configure_debug_capture(entry.options)
        self._device_id = get_client_device_id(config)
        self._session_store = AccountSessionStore(
            hass,
            get_session_store_id(config),
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
            account_session = await self._session_store.async_load()
            if account_session is not None:
                self._add_session_redactions(account_session)
            api = EcovacsMowerApi(
                aiohttp_client.async_get_clientsession(self._hass),
                username=self._config[CONF_USERNAME],
                password=self._config[CONF_PASSWORD],
                country=self._config[CONF_COUNTRY],
                device_id=self._device_id,
                account_session=account_session,
                account_session_update_callback=(
                    self._async_account_session_updated
                ),
                debug_capture=self._debug_capture,
            )
            self._api = api
            await api.authenticate()
            devices = await api.get_devices()
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
        except EcovacsDeviceVerificationRequiredError as ex:
            raise ConfigEntryAuthFailed("ECOVACS device verification required") from ex
        except EcovacsInvalidAuthError as ex:
            raise ConfigEntryAuthFailed("Invalid ECOVACS credentials") from ex
        except EcovacsAuthError as ex:
            raise ConfigEntryNotReady("ECOVACS authentication unavailable") from ex
        except ConfigEntryNotReady:
            await self._stop_coordinators(started)
            raise
        except Exception as ex:
            await self._stop_coordinators(started)
            raise ConfigEntryNotReady("Error during ECOVACS mower setup") from ex

    async def teardown(self) -> None:
        """Disconnect controller."""
        self._accept_session_updates = False
        # Drain a write that passed the callback gate before entry removal can
        # delete the store. Later callbacks observe the disabled gate.
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

    async def _stop_coordinators(
        self, coordinators: list[MowerCoordinator]
    ) -> None:
        """Best-effort stop every coordinator that was already started."""
        results = await asyncio.gather(
            *(coordinator.async_stop() for coordinator in coordinators),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                _LOGGER.warning(
                    "Error stopping an ECOVACS mower coordinator",
                    exc_info=(type(result), result, result.__traceback__),
                )

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
