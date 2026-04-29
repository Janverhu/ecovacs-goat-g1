"""Mower-only ECOVACS GOAT G1 integration."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er

from .const import (
    DOMAIN,
    SERVICE_CLEAR_DEBUG_CAPTURE,
    SERVICE_EXPORT_DEBUG_CAPTURE,
    SERVICE_MARK_DEBUG_CAPTURE,
    SERVICE_REFRESH_STATE,
    SERVICE_REQUEST_LIVE_POSITION_STREAM,
    SERVICE_START_DEBUG_CAPTURE,
    SERVICE_STOP_DEBUG_CAPTURE,
)
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
REQUEST_LIVE_POSITION_STREAM_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional("reason"): cv.string,
        vol.Optional("force", default=False): cv.boolean,
        vol.Optional("duration_seconds"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=3600)
        ),
    }
)
DEBUG_CAPTURE_START_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional("reason"): cv.string,
        vol.Optional("include_raw_payloads"): cv.boolean,
        vol.Optional("duration_minutes"): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional("max_size_mb"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)
DEBUG_CAPTURE_MARK_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required("message"): cv.string,
    }
)
DEBUG_CAPTURE_EXPORT_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional("session_id"): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: EcovacsConfigEntry) -> bool:
    """Set up this integration using UI."""
    controller = EcovacsController(hass, entry)
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
    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_STATE):

        async def async_refresh_state(call: ServiceCall) -> None:
            """Refresh state if MQTT/readback data is stale."""
            coordinators = _coordinators_for_call(hass, call)
            await asyncio.gather(
                *(coordinator.async_refresh_if_stale() for coordinator in coordinators)
            )

        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH_STATE,
            async_refresh_state,
            schema=REFRESH_STATE_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_REQUEST_LIVE_POSITION_STREAM):

        async def async_request_live_position_stream(call: ServiceCall) -> None:
            """Request app-style fast live position updates for visible maps."""
            coordinators = _coordinators_for_call(hass, call)
            reason = call.data.get("reason") or "manual"
            force = call.data["force"]
            duration_seconds = call.data.get("duration_seconds")
            await asyncio.gather(
                *(
                    coordinator.async_request_live_position_stream(
                        reason,
                        force=force,
                        duration_seconds=duration_seconds,
                    )
                    for coordinator in coordinators
                )
            )

        hass.services.async_register(
            DOMAIN,
            SERVICE_REQUEST_LIVE_POSITION_STREAM,
            async_request_live_position_stream,
            schema=REQUEST_LIVE_POSITION_STREAM_SCHEMA,
        )

    if hass.services.has_service(DOMAIN, SERVICE_START_DEBUG_CAPTURE):
        return

    async def async_start_debug_capture(call: ServiceCall) -> None:
        """Start debug captures for matching config entries."""
        for controller in _controllers_for_call(hass, call):
            controller.debug_capture.start(
                reason=call.data.get("reason"),
                include_raw_payloads=call.data.get("include_raw_payloads"),
                max_duration_seconds=call.data.get("duration_minutes", 0) * 60
                if "duration_minutes" in call.data
                else None,
                max_bytes=call.data.get("max_size_mb", 0) * 1024 * 1024
                if "max_size_mb" in call.data
                else None,
            )

    async def async_stop_debug_capture(call: ServiceCall) -> None:
        """Stop debug captures for matching config entries."""
        for controller in _controllers_for_call(hass, call):
            controller.debug_capture.stop()

    async def async_clear_debug_capture(call: ServiceCall) -> None:
        """Clear debug captures for matching config entries."""
        for controller in _controllers_for_call(hass, call):
            controller.debug_capture.clear()

    async def async_mark_debug_capture(call: ServiceCall) -> None:
        """Add a marker to active debug captures."""
        for controller in _controllers_for_call(hass, call):
            controller.debug_capture.mark(call.data["message"])

    async def async_export_debug_capture(call: ServiceCall) -> dict[str, object]:
        """Export the latest debug capture as a local zip file."""
        try:
            exports = [
                controller.debug_capture.export_zip(call.data.get("session_id"))
                for controller in _controllers_for_call(hass, call)
            ]
        except FileNotFoundError as err:
            raise HomeAssistantError(str(err)) from err
        return {"exports": exports}

    hass.services.async_register(
        DOMAIN,
        SERVICE_START_DEBUG_CAPTURE,
        async_start_debug_capture,
        schema=DEBUG_CAPTURE_START_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_STOP_DEBUG_CAPTURE,
        async_stop_debug_capture,
        schema=REFRESH_STATE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_DEBUG_CAPTURE,
        async_clear_debug_capture,
        schema=REFRESH_STATE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_MARK_DEBUG_CAPTURE,
        async_mark_debug_capture,
        schema=DEBUG_CAPTURE_MARK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_DEBUG_CAPTURE,
        async_export_debug_capture,
        schema=DEBUG_CAPTURE_EXPORT_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


def _controllers_for_call(
    hass: HomeAssistant, call: ServiceCall
) -> list[EcovacsController]:
    """Return controllers matching an optional entity target."""
    entity_ids = set(call.data.get(ATTR_ENTITY_ID, []))
    device_ids = _device_ids_for_entities(hass, entity_ids) if entity_ids else None
    controllers: list[EcovacsController] = []

    for config_entry in hass.config_entries.async_entries(DOMAIN):
        controller = getattr(config_entry, "runtime_data", None)
        if controller is None:
            continue
        if device_ids is None or any(
            coordinator.device.did in device_ids for coordinator in controller.coordinators
        ):
            controllers.append(controller)

    if entity_ids and not controllers:
        raise HomeAssistantError("No matching ECOVACS GOAT G1 entities found")

    return controllers


def _coordinators_for_call(hass: HomeAssistant, call: ServiceCall) -> list[Any]:
    """Return coordinators matching an optional entity target."""
    entity_ids = set(call.data.get(ATTR_ENTITY_ID, []))
    device_ids = _device_ids_for_entities(hass, entity_ids) if entity_ids else None
    coordinators = []

    for controller in _controllers_for_call(hass, call):
        for coordinator in controller.coordinators:
            if device_ids is None or coordinator.device.did in device_ids:
                coordinators.append(coordinator)

    return coordinators


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
