"""Mower-only ECOVACS GOAT G1 integration."""

from __future__ import annotations

import asyncio

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er

from .const import DOMAIN, SERVICE_REFRESH_STATE
from .controller import EcovacsController

PLATFORMS = [
    Platform.BUTTON,
    Platform.LAWN_MOWER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

type EcovacsConfigEntry = ConfigEntry[EcovacsController]

REFRESH_STATE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: EcovacsConfigEntry) -> bool:
    """Set up this integration using UI."""
    controller = EcovacsController(hass, entry.data)
    await controller.initialize()
    entry.runtime_data = controller

    _async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EcovacsConfigEntry) -> bool:
    """Unload config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.teardown()
    return unload_ok


def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services."""
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH_STATE):
        return

    async def async_refresh_state(call: ServiceCall) -> None:
        """Refresh state if MQTT/readback data is stale."""
        entity_ids = set(call.data.get(ATTR_ENTITY_ID, []))
        device_ids = _device_ids_for_entities(hass, entity_ids) if entity_ids else None
        coordinators = []

        for config_entry in hass.config_entries.async_entries(DOMAIN):
            controller = getattr(config_entry, "runtime_data", None)
            if controller is None:
                continue
            for coordinator in controller.coordinators:
                if device_ids is None or coordinator.device.did in device_ids:
                    coordinators.append(coordinator)

        if entity_ids and not coordinators:
            raise HomeAssistantError("No matching ECOVACS GOAT G1 entities found")

        await asyncio.gather(
            *(coordinator.async_refresh_if_stale() for coordinator in coordinators)
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_STATE,
        async_refresh_state,
        schema=REFRESH_STATE_SCHEMA,
    )


def _device_ids_for_entities(hass: HomeAssistant, entity_ids: set[str]) -> set[str]:
    """Resolve GOAT device ids from Home Assistant entity ids."""
    registry = er.async_get(hass)
    device_ids: set[str] = set()
    invalid_entity_ids: list[str] = []

    for entity_id in entity_ids:
        entity_entry = registry.async_get(entity_id)
        if (
            entity_entry is None
            or entity_entry.platform != DOMAIN
            or not entity_entry.unique_id
        ):
            invalid_entity_ids.append(entity_id)
            continue
        device_ids.add(entity_entry.unique_id.split("_", 1)[0])

    if invalid_entity_ids:
        raise HomeAssistantError(
            "Entities are not ECOVACS GOAT G1 entities: "
            + ", ".join(sorted(invalid_entity_ids))
        )

    return device_ids
