"""Auto-register and version the bundled Lovelace card.

The integration ships its dashboard card (``frontend/ecovacs-goat-card.js``) and
serves it over HTTP so users do not have to copy the file into ``/config/www``
or add a Lovelace resource by hand. The card is loaded as a frontend module with
the integration version as a ``?v=`` cache-busting token, so when the integration
is updated the browser automatically fetches the new card on the next load.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_FILENAME = "ecovacs-goat-card.js"
CARD_URL_PATH = f"/{DOMAIN}/{CARD_FILENAME}"
_REGISTERED_KEY = f"{DOMAIN}_frontend_registered"


async def async_register_frontend_card(hass: HomeAssistant) -> None:
    """Serve the bundled card and auto-load it (versioned for cache-busting).

    Registration is process-global and idempotent: HTTP static paths can only be
    registered once, so repeated setups (multiple entries, reloads) are no-ops.
    """
    if hass.data.get(_REGISTERED_KEY):
        return

    card_path = Path(__file__).parent / "frontend" / CARD_FILENAME
    if not await hass.async_add_executor_job(card_path.is_file):
        _LOGGER.warning("ECOVACS GOAT card asset missing at %s", card_path)
        return

    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL_PATH, str(card_path), False)]
        )
    except RuntimeError as err:
        # Already registered (e.g. a prior failed setup); treat as done.
        _LOGGER.debug("ECOVACS GOAT card static path already registered: %s", err)

    version = await _async_card_version(hass)
    add_extra_js_url(hass, f"{CARD_URL_PATH}?v={version}")
    hass.data[_REGISTERED_KEY] = True
    _LOGGER.debug("Registered ECOVACS GOAT card at %s?v=%s", CARD_URL_PATH, version)


async def _async_card_version(hass: HomeAssistant) -> str:
    """Return the integration version used to cache-bust the card URL."""
    try:
        integration = await async_get_integration(hass, DOMAIN)
    except Exception:  # noqa: BLE001 - version is best-effort cache busting only
        return "0"
    return str(integration.version or "0")
