"""Config flow for the ECOVACS GOAT mower integration."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientError
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_COUNTRY, CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client, selector
from homeassistant.helpers.typing import VolDictType

from .const import DOMAIN
from .mower_api import EcovacsApiError, EcovacsAuthError, EcovacsMowerApi
from .util import get_client_device_id

_LOGGER = logging.getLogger(__name__)
DEFAULT_NAME_PREFIX = "Ecovacs-GOAT"


async def _validate_input(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> dict[str, str]:
    """Validate user input."""
    errors: dict[str, str] = {}
    if not str(user_input.get(CONF_NAME, "")).strip():
        errors[CONF_NAME] = "invalid_name"
        return errors

    device_id = get_client_device_id(hass)
    api = EcovacsMowerApi(
        aiohttp_client.async_get_clientsession(hass),
        username=user_input[CONF_USERNAME],
        password=user_input[CONF_PASSWORD],
        country=user_input[CONF_COUNTRY],
        device_id=device_id,
    )

    try:
        await api.authenticate()
        devices = await api.get_devices()
    except EcovacsAuthError:
        errors["base"] = "invalid_auth"
    except (ClientError, EcovacsApiError):
        _LOGGER.debug("Cannot connect", exc_info=True)
        errors["base"] = "cannot_connect"
    except Exception:
        _LOGGER.exception("Unexpected exception during login")
        errors["base"] = "unknown"

    if not errors and not devices:
        errors["base"] = "unknown"

    return errors


class EcovacsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ecovacs."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input:
            self._async_abort_entries_match({CONF_USERNAME: user_input[CONF_USERNAME]})
            user_input = {
                **user_input,
                CONF_NAME: str(user_input[CONF_NAME]).strip(),
            }

            errors = await _validate_input(self.hass, user_input)

            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=user_input
                )

        schema: VolDictType = {
            vol.Required(CONF_NAME): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            vol.Required(CONF_USERNAME): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            vol.Required(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_COUNTRY): selector.CountrySelector(),
        }

        if not user_input:
            user_input = {
                CONF_NAME: self._suggested_name(),
                CONF_COUNTRY: self.hass.config.country,
            }

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                data_schema=vol.Schema(schema), suggested_values=user_input
            ),
            errors=errors,
            last_step=True,
        )

    def _suggested_name(self) -> str:
        """Return the next generic GOAT entry name."""
        existing = {
            entry.title
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        }
        number = 1
        while f"{DEFAULT_NAME_PREFIX}-{number}" in existing:
            number += 1
        return f"{DEFAULT_NAME_PREFIX}-{number}"
