"""Config flow for the ECOVACS GOAT mower integration."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientError
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_COUNTRY, CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client, selector
from homeassistant.helpers.typing import VolDictType

from .const import (
    CONF_DEVICE_ID,
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
    EcovacsApiError,
    EcovacsDeviceVerificationRequiredError,
    EcovacsInvalidAuthError,
    EcovacsInvalidVerificationCodeError,
    EcovacsMowerApi,
)
from .session_store import AccountSessionStore
from .util import (
    generate_client_device_id,
    generate_session_store_id,
    get_client_device_id,
    get_session_store_id,
)

_LOGGER = logging.getLogger(__name__)
DEFAULT_NAME_PREFIX = "Ecovacs-GOAT"


async def _create_api(
    hass: HomeAssistant,
    data: dict[str, Any],
    *,
    use_stored_session: bool = True,
) -> tuple[EcovacsMowerApi, AccountSessionStore]:
    """Create an API client from pending or persisted config-entry data."""
    session_store = AccountSessionStore(
        hass,
        get_session_store_id(data),
        get_client_device_id(data),
        str(data[CONF_USERNAME]),
        str(data[CONF_COUNTRY]),
    )
    account_session = (
        await session_store.async_load() if use_stored_session else None
    )
    session_update_callback = None
    if account_session is not None:
        persist_seeded_session_updates = True

        async def _async_persist_seeded_session_update(
            updated_session: AccountSession | None,
        ) -> None:
            """Persist rotations/clears, but defer password fallback sessions."""
            nonlocal persist_seeded_session_updates
            if not persist_seeded_session_updates:
                return
            await session_store.async_save(updated_session)
            if updated_session is None:
                persist_seeded_session_updates = False

        session_update_callback = _async_persist_seeded_session_update

    return EcovacsMowerApi(
        aiohttp_client.async_get_clientsession(hass),
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        country=data[CONF_COUNTRY],
        device_id=get_client_device_id(data),
        account_session=account_session,
        account_session_update_callback=session_update_callback,
    ), session_store


def _enable_verified_session_persistence(
    api: EcovacsMowerApi, session_store: AccountSessionStore
) -> None:
    """Persist a session returned after the user submits a single-use OTP."""

    async def _async_persist_verified_session(
        updated_session: AccountSession | None,
    ) -> None:
        if updated_session is None:
            await session_store.async_save(None)
            return
        await session_store.async_save_verified(updated_session)

    api.set_account_session_update_callback(_async_persist_verified_session)


async def _authenticate_and_find_devices(api: EcovacsMowerApi) -> bool:
    """Authenticate and report whether the account contains a GOAT mower."""
    await api.authenticate()
    return bool(await api.get_devices())


async def _persist_authenticated_session(
    api: EcovacsMowerApi, session_store: AccountSessionStore
) -> None:
    """Confirm the authenticated session is durable before completing setup."""
    if api.account_session is None:
        raise EcovacsInvalidAuthError("ECOVACS account session is missing")
    await session_store.async_save_verified(api.account_session)


class EcovacsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ecovacs."""

    VERSION = 4

    def __init__(self) -> None:
        """Initialize flow-local verification state."""
        self._verification_api: EcovacsMowerApi | None = None
        self._verification_data: dict[str, Any] | None = None
        self._verification_store: AccountSessionStore | None = None
        self._reauth_entry: ConfigEntry | None = None

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> EcovacsOptionsFlow:
        """Create the options flow."""
        return EcovacsOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle initial account setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._async_abort_entries_match({CONF_USERNAME: user_input[CONF_USERNAME]})
            data = {
                **user_input,
                CONF_NAME: str(user_input[CONF_NAME]).strip(),
                CONF_DEVICE_ID: generate_client_device_id(),
                CONF_SESSION_STORE_ID: generate_session_store_id(),
            }
            if not data[CONF_NAME]:
                errors[CONF_NAME] = "invalid_name"
            else:
                api, session_store = await _create_api(self.hass, data)
                try:
                    has_devices = await _authenticate_and_find_devices(api)
                    if has_devices:
                        await _persist_authenticated_session(api, session_store)
                except EcovacsDeviceVerificationRequiredError:
                    await session_store.async_save(None)
                    _enable_verified_session_persistence(api, session_store)
                    try:
                        await api.request_device_verification_code()
                    except EcovacsInvalidAuthError:
                        errors["base"] = "invalid_auth"
                    except (ClientError, EcovacsApiError):
                        _LOGGER.debug(
                            "Cannot request ECOVACS device verification",
                            exc_info=True,
                        )
                        errors["base"] = "cannot_connect"
                    except Exception:
                        _LOGGER.exception(
                            "Unexpected exception requesting ECOVACS verification"
                        )
                        errors["base"] = "unknown"
                    else:
                        self._verification_api = api
                        self._verification_data = data
                        self._verification_store = session_store
                        return await self.async_step_device_verification()
                except EcovacsInvalidAuthError:
                    await session_store.async_save(None)
                    errors["base"] = "invalid_auth"
                except (ClientError, EcovacsApiError):
                    _LOGGER.debug("Cannot connect to ECOVACS", exc_info=True)
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected exception during ECOVACS login")
                    errors["base"] = "unknown"
                else:
                    if has_devices:
                        return self.async_create_entry(title=data[CONF_NAME], data=data)
                    await session_store.async_save(None)
                    errors["base"] = "no_devices"

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

        suggested_values = user_input or {
            CONF_NAME: self._suggested_name(),
            CONF_COUNTRY: self.hass.config.country,
        }
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                data_schema=vol.Schema(schema), suggested_values=suggested_values
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication for a previously configured account."""
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Request an email code only after the user confirms the repair."""
        if self._reauth_entry is None:
            return self.async_abort(reason="verification_session_expired")

        errors: dict[str, str] = {}
        if user_input is not None:
            data = dict(self._reauth_entry.data)
            if new_password := str(user_input.get(CONF_PASSWORD, "")):
                data[CONF_PASSWORD] = new_password
            data[CONF_DEVICE_ID] = get_client_device_id(data)
            data[CONF_SESSION_STORE_ID] = get_session_store_id(data)
            api, session_store = await _create_api(
                self.hass,
                data,
                use_stored_session=not bool(new_password),
            )
            try:
                has_devices = await _authenticate_and_find_devices(api)
                if has_devices:
                    await _persist_authenticated_session(api, session_store)
            except EcovacsDeviceVerificationRequiredError:
                await session_store.async_save(None)
                _enable_verified_session_persistence(api, session_store)
                try:
                    await api.request_device_verification_code()
                except EcovacsInvalidAuthError:
                    errors["base"] = "invalid_auth"
                except (ClientError, EcovacsApiError):
                    _LOGGER.debug(
                        "Cannot request ECOVACS device verification",
                        exc_info=True,
                    )
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception(
                        "Unexpected exception requesting ECOVACS verification"
                    )
                    errors["base"] = "unknown"
                else:
                    self._verification_api = api
                    self._verification_data = data
                    self._verification_store = session_store
                    return await self.async_step_device_verification()
            except EcovacsInvalidAuthError:
                await session_store.async_save(None)
                errors["base"] = "invalid_auth"
            except (ClientError, EcovacsApiError):
                _LOGGER.debug("Cannot connect to ECOVACS", exc_info=True)
                if result := await self._async_complete_with_durable_session(
                    api, session_store, data
                ):
                    return result
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception during ECOVACS login")
                if result := await self._async_complete_with_durable_session(
                    api, session_store, data
                ):
                    return result
                errors["base"] = "unknown"
            else:
                if has_devices:
                    return self.async_update_reload_and_abort(
                        self._reauth_entry,
                        data_updates=data,
                    )
                await session_store.async_save(None)
                errors["base"] = "no_devices"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "account": str(self._reauth_entry.data[CONF_USERNAME])
            },
        )

    async def async_step_device_verification(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify the stable device id with the emailed one-time code."""
        if (
            self._verification_api is None
            or self._verification_data is None
            or self._verification_store is None
        ):
            return self.async_abort(reason="verification_session_expired")

        errors: dict[str, str] = {}
        if user_input is not None:
            verification_code = str(user_input[CONF_VERIFICATION_CODE]).strip()
            if not verification_code:
                errors[CONF_VERIFICATION_CODE] = "invalid_verification_code"
            else:
                try:
                    await self._verification_api.verify_device(verification_code)
                    has_devices = bool(await self._verification_api.get_devices())
                    if has_devices:
                        await _persist_authenticated_session(
                            self._verification_api,
                            self._verification_store,
                        )
                except EcovacsInvalidVerificationCodeError:
                    errors["base"] = "invalid_verification_code"
                except EcovacsDeviceVerificationRequiredError:
                    await self._verification_store.async_save(None)
                    errors["base"] = "invalid_verification_code"
                except EcovacsInvalidAuthError:
                    await self._verification_store.async_save(None)
                    errors["base"] = "invalid_auth"
                except (ClientError, EcovacsApiError):
                    _LOGGER.debug(
                        "Cannot verify the ECOVACS client device", exc_info=True
                    )
                    if result := await self._async_complete_with_durable_session(
                        self._verification_api,
                        self._verification_store,
                        self._verification_data,
                    ):
                        return result
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception(
                        "Unexpected exception verifying the ECOVACS client device"
                    )
                    if result := await self._async_complete_with_durable_session(
                        self._verification_api,
                        self._verification_store,
                        self._verification_data,
                    ):
                        return result
                    errors["base"] = "unknown"
                else:
                    if not has_devices:
                        await self._verification_store.async_save(None)
                        errors["base"] = "no_devices"
                    if has_devices and self._reauth_entry is not None:
                        return self.async_update_reload_and_abort(
                            self._reauth_entry,
                            data_updates=self._verification_data,
                        )
                    if has_devices:
                        return self.async_create_entry(
                            title=self._verification_data[CONF_NAME],
                            data=self._verification_data,
                        )

        schema = vol.Schema(
            {
                vol.Required(CONF_VERIFICATION_CODE): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.PASSWORD
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="device_verification",
            data_schema=schema,
            errors=errors,
            last_step=True,
        )

    async def _async_complete_with_durable_session(
        self,
        api: EcovacsMowerApi,
        session_store: AccountSessionStore,
        data: dict[str, Any],
    ) -> ConfigFlowResult | None:
        """Bind an acquired raw session to an entry after a later API failure."""
        if api.account_session is None:
            return None
        try:
            await session_store.async_save_verified(api.account_session)
        except Exception:
            _LOGGER.exception("Cannot persist the ECOVACS account session")
            return None
        if self._reauth_entry is not None:
            return self.async_update_reload_and_abort(
                self._reauth_entry,
                data_updates=data,
            )
        return self.async_create_entry(title=data[CONF_NAME], data=data)

    def _suggested_name(self) -> str:
        """Return the next generic GOAT entry name."""
        existing = {
            entry.title for entry in self.hass.config_entries.async_entries(DOMAIN)
        }
        number = 1
        while f"{DEFAULT_NAME_PREFIX}-{number}" in existing:
            number += 1
        return f"{DEFAULT_NAME_PREFIX}-{number}"


class EcovacsOptionsFlow(OptionsFlow):
    """Handle ECOVACS GOAT options."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage integration options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self._config_entry.options
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
            step_id="init",
            data_schema=vol.Schema(schema),
            description_placeholders={
                "warning": (
                    "Debug captures may include mower map, position, and raw cloud "
                    "payload data. Account and device identifiers are redacted."
                )
            },
        )
