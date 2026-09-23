"""Auto-register and version the bundled Lovelace card.

The integration ships its dashboard card (``frontend/ecovacs-goat-card.js``) and
serves it over HTTP so users do not have to copy the file into ``/config/www``
or add a Lovelace resource by hand. Storage-mode Lovelace gets that URL as a
module dashboard resource, which Lovelace loads before it creates cards. The
``?v=`` token is a hash of the card file, so the browser fetches a new card
whenever the file changes. YAML-only resource mode uses an extra frontend
module URL for the same file.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_STORAGE
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_FILENAME = "ecovacs-goat-card.js"
CARD_URL_PATH = f"/{DOMAIN}/{CARD_FILENAME}"
_REGISTERED_KEY = f"{DOMAIN}_frontend_registered"
_REGISTER_LOCK_KEY = f"{DOMAIN}_frontend_register_lock"


async def async_register_frontend_card(hass: HomeAssistant) -> None:
    """Serve the bundled card and register it before Lovelace creates cards.

    Registration is process-global and idempotent: HTTP static paths can only be
    registered once, so repeated setups (multiple entries, reloads) are no-ops.
    """
    lock: asyncio.Lock = hass.data.setdefault(_REGISTER_LOCK_KEY, asyncio.Lock())
    async with lock:
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

        token = await _async_card_cache_token(hass, card_path)
        url = f"{CARD_URL_PATH}?v={token}"
        if not await _async_register_lovelace_module(hass, url):
            add_extra_js_url(hass, url)
        hass.data[_REGISTERED_KEY] = True
        _LOGGER.info(
            "Registered ECOVACS GOAT dashboard card at %s?v=%s", CARD_URL_PATH, token
        )


async def _async_register_lovelace_module(hass: HomeAssistant, url: str) -> bool:
    """Register ``url`` as a storage-mode Lovelace module resource.

    Returns True when storage mode handled the URL. Returns False when Lovelace
    data is missing or resources are YAML-only, so the caller can fall back to
    ``add_extra_js_url``.
    """
    lovelace_data = hass.data.get(LOVELACE_DATA)
    if lovelace_data is None:
        return False
    if getattr(lovelace_data, "resource_mode", None) != MODE_STORAGE:
        return False
    resources = getattr(lovelace_data, "resources", None)
    if resources is None:
        return False

    try:
        await _async_ensure_storage_module(resources, url)
    except Exception:  # noqa: BLE001 - card registration must not block setup
        _LOGGER.warning(
            "Could not register the ECOVACS GOAT card as a Lovelace resource; "
            "loading it as an extra frontend module",
            exc_info=True,
        )
        return False
    return True


async def _async_ensure_storage_module(resources: Any, url: str) -> None:
    """Create or refresh the bundled card module resource.

    Matches on the card path and ignores the query string. Other resources,
    including a legacy ``/local/ecovacs_goat/ecovacs-goat-card.js`` entry, stay
    as they are.
    """
    await resources.async_get_info()
    target_query = urlsplit(url).query
    matches = [
        item
        for item in resources.async_items()
        if isinstance(item, dict) and _is_bundled_card_url(item.get("url"))
    ]
    if not matches:
        await resources.async_create_item({"res_type": "module", "url": url})
        return

    for item in matches:
        item_url = item.get("url")
        if isinstance(item_url, str) and urlsplit(item_url).query == target_query:
            continue
        item_id = item.get("id")
        if not item_id:
            _LOGGER.debug(
                "Skipping ECOVACS GOAT card resource without an id: %s", item_url
            )
            continue
        await resources.async_update_item(item_id, {"url": url})


def _is_bundled_card_url(url: object) -> bool:
    """Return True when ``url`` is the bundled card, ignoring its query."""
    if not isinstance(url, str) or not url:
        return False
    try:
        path = urlsplit(url).path
    except ValueError:
        return False
    return path == CARD_URL_PATH


async def _async_card_cache_token(hass: HomeAssistant, card_path: Path) -> str:
    """Return a cache-busting token derived from the card file contents.

    Hashing the file (rather than using the integration version) guarantees the
    browser refetches the card whenever the file actually changes - including
    HACS beta updates that do not bump ``manifest.json`` - and never otherwise.
    Falls back to the integration version if the file cannot be read.
    """

    def _hash() -> str:
        return hashlib.sha256(card_path.read_bytes()).hexdigest()[:12]

    try:
        return await hass.async_add_executor_job(_hash)
    except OSError:
        # Cache busting is best-effort only; fall back to the integration version.
        return await _async_card_version(hass)


async def _async_card_version(hass: HomeAssistant) -> str:
    """Return the integration version (fallback cache-bust token)."""
    try:
        integration = await async_get_integration(hass, DOMAIN)
    except Exception:  # noqa: BLE001 - version is best-effort cache busting only
        return "0"
    return str(integration.version or "0")
