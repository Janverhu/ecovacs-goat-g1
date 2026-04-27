"""Ecovacs util functions."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.util import slugify


def get_client_device_id(hass: HomeAssistant) -> str:
    """Get client device id."""
    return f"HA-{slugify(hass.config.location_name)}"
