"""Ecovacs diagnostics."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import EcovacsConfigEntry

REDACT_CONFIG = {
    CONF_USERNAME,
    CONF_PASSWORD,
    "title",
}
REDACT_DEVICE = {"did", "name", "nick", "homeId"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: EcovacsConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    controller = config_entry.runtime_data
    diag: dict[str, Any] = {
        "config": async_redact_data(config_entry.as_dict(), REDACT_CONFIG)
    }

    diag["devices"] = [
        async_redact_data(device, REDACT_DEVICE)
        for device in controller.devices
    ]
    diag["debug_capture"] = {
        "summary": controller.debug_capture.summary(),
        "recent_events": controller.debug_capture.recent_events(limit=100),
    }

    return diag
