"""Controller for the mower-only ECOVACS integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import logging
from typing import Any

from homeassistant.const import CONF_COUNTRY, CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import aiohttp_client

from .mower_api import EcovacsAuthError, EcovacsMowerApi
from .mower_coordinator import MowerCoordinator
from .util import get_client_device_id

_LOGGER = logging.getLogger(__name__)


class EcovacsController:
    """Mower-only ECOVACS controller."""

    def __init__(self, hass: HomeAssistant, config: Mapping[str, Any]) -> None:
        """Initialize controller."""
        self._hass = hass
        self._configured_name = str(config.get(CONF_NAME) or "Ecovacs-GOAT")
        self._api = EcovacsMowerApi(
            aiohttp_client.async_get_clientsession(hass),
            username=config[CONF_USERNAME],
            password=config[CONF_PASSWORD],
            country=config[CONF_COUNTRY],
            device_id=get_client_device_id(hass),
        )
        self._coordinators: list[MowerCoordinator] = []

    async def initialize(self) -> None:
        """Initialize mower devices and coordinators."""
        started: list[MowerCoordinator] = []
        try:
            await self._api.authenticate()
            devices = await self._api.get_devices()
            if not devices:
                raise ConfigEntryNotReady("No ECOVACS mower devices found")

            for device in devices:
                device = replace(device, name=self._configured_name)
                coordinator = MowerCoordinator(
                    self._hass,
                    self._api,
                    device,
                )
                await coordinator.async_start()
                started.append(coordinator)
                _LOGGER.info("Initialized ECOVACS mower %s", device.name)
            self._coordinators = started
        except EcovacsAuthError as ex:
            raise ConfigEntryError("Invalid ECOVACS credentials") from ex
        except ConfigEntryNotReady:
            await self._stop_coordinators(started)
            raise
        except Exception as ex:
            await self._stop_coordinators(started)
            raise ConfigEntryNotReady("Error during ECOVACS mower setup") from ex

    async def teardown(self) -> None:
        """Disconnect controller."""
        await self._stop_coordinators(self._coordinators)
        self._coordinators.clear()

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
