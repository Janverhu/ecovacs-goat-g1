"""Ask the user to sign in again after an ECOVACS login failure."""

from __future__ import annotations

import logging

from homeassistant.components.persistent_notification import async_create, async_dismiss
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .mower_api import EcovacsMowerApi

_LOGGER = logging.getLogger(__name__)


def _notification_id(entry: ConfigEntry) -> str:
    return f"{DOMAIN}_reauth_{entry.entry_id}"


def async_publish_reauth_required(
    hass: HomeAssistant,
    entry: ConfigEntry,
    api: EcovacsMowerApi,
    err: BaseException,
) -> None:
    """Log the failure once and start Home Assistant's reauthentication flow.

    Further unattended login attempts are blocked by the API. The persistent
    notification and the repair created by the reauth flow are how the user
    finds out they need to sign in again.
    """
    api.mark_reauth_required()
    if not api.consume_reauth_notification():
        return
    _LOGGER.error(
        "ECOVACS login failed for %s. Automatic login will not be retried. "
        "Re-authenticate the integration in Settings → Devices & services.",
        entry.title,
    )
    _LOGGER.debug("ECOVACS login failure detail", exc_info=err)
    async_create(
        hass,
        (
            f"{entry.title} could not sign in to ECOVACS. "
            "Automatic login will not be retried, so the mower is left alone. "
            "Open Settings → Devices & services → Ecovacs GOAT G1 and re-authenticate."
        ),
        title="Ecovacs GOAT sign-in required",
        notification_id=_notification_id(entry),
    )
    entry.async_start_reauth(hass)


def async_dismiss_reauth_notification(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the sign-in notification after a successful setup."""
    async_dismiss(hass, _notification_id(entry))
