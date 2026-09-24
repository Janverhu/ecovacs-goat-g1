"""Config flow for the ECOVACS GOAT mower integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from aiohttp import ClientError
import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_COUNTRY, CONF_DEVICE_ID, CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client, selector
from homeassistant.helpers.typing import VolDictType

from .const import (
    CONF_SESSION_STORE_ID,
    CONF_VERIFICATION_CODE,
    DEFAULT_DEBUG_CAPTURE_MAX_DURATION_MINUTES,
    DEFAULT_DEBUG_CAPTURE_MAX_SIZE_MB,
    DEFAULT_DEBUG_CAPTURE_RAW_PAYLOADS,
    DOMAIN,
    OPTION_DEBUG_CAPTURE_MAX_DURATION_MINUTES,
    OPTION_DEBUG_CAPTURE_MAX_SIZE_MB,
    OPTION_DEBUG_CAPTURE_RAW_PAYLOADS,
)
from .mower_api import (
    AccountSession,
    DeviceVerificationRequiredError,
    EcovacsApiError,
    EcovacsAuthError,
    EcovacsMowerApi,
    InvalidVerificationCodeError,
)
from .session_store import AccountSessionStore, async_remove_account_session_store
from .util import get_client_device_id, get_session_store_id

_LOGGER = logging.getLogger(__name__)
DEFAULT_NAME_PREFIX = "Ecovacs-GOAT"


def _create_api(
    hass: HomeAssistant,
    user_input: Mapping[str, Any],
    *,
    store: AccountSessionStore | None = None,
    account_session: AccountSession | None = None,
) -> EcovacsMowerApi:
    """Create an API client for the current flow input."""

    async def _persist(session: AccountSession | None) -> None:
        if store is None:
            return
        await store.async_save(session)

    return EcovacsMowerApi(
        aiohttp_client.async_get_clientsession(hass),
        username=user_input[CONF_USERNAME],
        password=user_input[CONF_PASSWORD],
        country=user_input[CONF_COUNTRY],
        device_id=get_client_device_id(user_input),
        account_session=account_session,
        account_session_update_callback=_persist,
    )


async def _login_and_list_devices(api: EcovacsMowerApi) -> dict[str, str]:
    """Authenticate and confirm the account has at least one mower."""
    errors: dict[str, str] = {}
    try:
        await api.authenticate()
        devices = await api.get_devices()
    except DeviceVerificationRequiredError:
        raise
    except EcovacsAuthError:
        errors["base"] = "invalid_auth"
    except (ClientError, EcovacsApiError):
        _LOGGER.debug("Cannot connect", exc_info=True)
        errors["base"] = "cannot_connect"
    except Exception:
        _LOGGER.exception("Unexpected exception during login")
        errors["base"] = "unknown"
    else:
        if not devices:
            errors["base"] = "no_mower"
    return errors


class EcovacsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ecovacs."""

    VERSION = 1
    MINOR_VERSION = 3

    def __init__(self) -> None:
        """Initialize flow state."""
        self._input: dict[str, Any] = {}
        self._api: EcovacsMowerApi | None = None
        self._session_store: AccountSessionStore | None = None

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> EcovacsOptionsFlow:
        """Create the options flow."""
        return EcovacsOptionsFlow()

    def _bind_store(self, user_input: dict[str, Any]) -> AccountSessionStore:
        """Create the private store for this flow's device and account."""
        user_input[CONF_DEVICE_ID] = get_client_device_id(user_input)
        user_input[CONF_SESSION_STORE_ID] = get_session_store_id(user_input)
        self._session_store = AccountSessionStore(
            self.hass,
            user_input[CONF_SESSION_STORE_ID],
            user_input[CONF_DEVICE_ID],
            str(user_input[CONF_USERNAME]),
            str(user_input[CONF_COUNTRY]),
        )
        return self._session_store

    async def _store_api(self, user_input: dict[str, Any]) -> EcovacsMowerApi:
        """Create and remember the API client for later verification."""
        store = self._bind_store(user_input)
        session = await store.async_load()
        self._input = user_input
        self._api = _create_api(
            self.hass, user_input, store=store, account_session=session
        )
        return self._api

    def _entry_data(self) -> dict[str, Any]:
        """Build config entry data without account tokens."""
        return {
            CONF_NAME: str(self._input[CONF_NAME]).strip(),
            CONF_USERNAME: self._input[CONF_USERNAME],
            CONF_PASSWORD: self._input[CONF_PASSWORD],
            CONF_COUNTRY: self._input[CONF_COUNTRY],
            CONF_DEVICE_ID: self._input[CONF_DEVICE_ID],
            CONF_SESSION_STORE_ID: self._input[CONF_SESSION_STORE_ID],
        }

    async def _finish_flow(self) -> ConfigFlowResult:
        """Persist the session privately, then create or update the entry."""
        if self._session_store is not None and self._api is not None:
            if self._api.account_session is not None:
                await self._session_store.async_save_verified(self._api.account_session)
        data = self._entry_data()
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates=data
            )
        return self.async_create_entry(title=data[CONF_NAME], data=data)

    async def _async_request_device_verification_code(
        self, api: EcovacsMowerApi
    ) -> dict[str, str]:
        """Request a device verification code."""
        try:
            await api.request_device_verification_code()
        except ClientError:
            _LOGGER.debug("Cannot request ECOVACS verification code", exc_info=True)
            return {"base": "cannot_connect"}
        except EcovacsAuthError:
            _LOGGER.debug("ECOVACS rejected the verification-code request", exc_info=True)
            return {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected exception requesting verification code")
            return {"base": "unknown"}
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input:
            self._async_abort_entries_match({CONF_USERNAME: user_input[CONF_USERNAME]})
            user_input = {
                **user_input,
                CONF_NAME: str(user_input[CONF_NAME]).strip(),
            }
            if not user_input[CONF_NAME]:
                errors[CONF_NAME] = "invalid_name"
            else:
                api = await self._store_api(user_input)
                try:
                    errors = await _login_and_list_devices(api)
                except DeviceVerificationRequiredError:
                    errors = await self._async_request_device_verification_code(api)
                    if not errors:
                        return await self.async_step_device_verification()
                if not errors:
                    return await self._finish_flow()

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
            last_step=False,
        )

    async def async_step_device_verification(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify the stable ECOVACS client device id."""
        errors: dict[str, str] = {}
        if user_input and (api := self._api):
            try:
                await api.verify_device(user_input[CONF_VERIFICATION_CODE])
                devices = await api.get_devices()
            except InvalidVerificationCodeError:
                errors["base"] = "invalid_verification_code"
            except DeviceVerificationRequiredError:
                errors["base"] = "invalid_verification_code"
            except EcovacsAuthError:
                errors["base"] = "invalid_auth"
            except (ClientError, EcovacsApiError):
                _LOGGER.debug("Cannot verify ECOVACS device", exc_info=True)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception verifying ECOVACS device")
                errors["base"] = "unknown"
            else:
                if not devices:
                    errors["base"] = "no_mower"
                else:
                    return await self._finish_flow()

        return self.async_show_form(
            step_id="device_verification",
            data_schema=self.add_suggested_values_to_schema(
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_VERIFICATION_CODE): selector.TextSelector(
                            selector.TextSelectorConfig(
                                type=selector.TextSelectorType.TEXT
                            )
                        )
                    }
                ),
                suggested_values=user_input,
            ),
            description_placeholders={
                CONF_USERNAME: str(self._input.get(CONF_USERNAME, ""))
            },
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthentication."""
        self._input = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm credentials and verify a new device id if required."""
        errors: dict[str, str] = {}
        if user_input:
            api = await self._store_api({**self._input, **user_input})
            try:
                errors = await _login_and_list_devices(api)
            except DeviceVerificationRequiredError:
                errors = await self._async_request_device_verification_code(api)
                if not errors:
                    return await self.async_step_device_verification()
            if not errors:
                return await self._finish_flow()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_PASSWORD): selector.TextSelector(
                            selector.TextSelectorConfig(
                                type=selector.TextSelectorType.PASSWORD
                            )
                        )
                    }
                ),
                suggested_values=user_input,
            ),
            description_placeholders={
                CONF_USERNAME: str(self._input.get(CONF_USERNAME, ""))
            },
            errors=errors,
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


class EcovacsOptionsFlow(OptionsFlow):
    """Handle ECOVACS GOAT options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose between debug options and a manual reauthentication."""
        return self.async_show_menu(
            step_id="init",
            menu_options={
                "configure": "Debug capture",
                "reauthenticate": "Re-authenticate account",
            },
        )

    async def async_step_reauthenticate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start the existing reauth flow after dropping the stored session."""
        if user_input is not None:
            await async_remove_account_session_store(
                self.hass, self.config_entry.data.get(CONF_SESSION_STORE_ID)
            )
            self.config_entry.async_start_reauth(self.hass)
            return self.async_abort(reason="reauth_started")

        return self.async_show_form(
            step_id="reauthenticate",
            data_schema=vol.Schema({}),
            description_placeholders={
                CONF_USERNAME: str(self.config_entry.data.get(CONF_USERNAME, ""))
            },
        )

    async def async_step_configure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage debug-capture options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        schema: VolDictType = {
            vol.Required(
                OPTION_DEBUG_CAPTURE_RAW_PAYLOADS,
                default=options.get(
                    OPTION_DEBUG_CAPTURE_RAW_PAYLOADS,
                    DEFAULT_DEBUG_CAPTURE_RAW_PAYLOADS,
                ),
            ): selector.BooleanSelector(),
            vol.Required(
                OPTION_DEBUG_CAPTURE_MAX_DURATION_MINUTES,
                default=options.get(
                    OPTION_DEBUG_CAPTURE_MAX_DURATION_MINUTES,
                    DEFAULT_DEBUG_CAPTURE_MAX_DURATION_MINUTES,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=120,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="min",
                )
            ),
            vol.Required(
                OPTION_DEBUG_CAPTURE_MAX_SIZE_MB,
                default=options.get(
                    OPTION_DEBUG_CAPTURE_MAX_SIZE_MB,
                    DEFAULT_DEBUG_CAPTURE_MAX_SIZE_MB,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=100,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="MB",
                )
            ),
        }

        return self.async_show_form(
            step_id="configure",
            data_schema=vol.Schema(schema),
            description_placeholders={
                "warning": (
                    "Debug captures may include mower map, position, and raw cloud "
                    "payload data. Account and device identifiers are redacted."
                )
            },
        )
