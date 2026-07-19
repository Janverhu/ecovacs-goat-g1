"""State-machine tests for the ECOVACS verification config flow."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import voluptuous as vol

PACKAGE_PATH = Path(__file__).parents[2] / "custom_components" / "ecovacs_goat_g1"


class _Selector:
    def __init__(self, _config: Any = None) -> None:
        pass

    def __call__(self, value: Any) -> Any:
        return value


class _SelectorConfig:
    def __init__(self, **_kwargs: Any) -> None:
        pass


class _ConfigEntry:
    def __init__(
        self,
        data: dict[str, Any],
        title: str = "GOAT",
        version: int = 3,
    ) -> None:
        self.data = data
        self.title = title
        self.version = version

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "data": dict(self.data)}


class _ConfigFlow:
    def __init_subclass__(cls, *, domain: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.domain = domain

    def _async_abort_entries_match(self, _match: dict[str, Any]) -> None:
        pass

    def _get_reauth_entry(self) -> _ConfigEntry:
        return self._test_reauth_entry

    @staticmethod
    def add_suggested_values_to_schema(
        data_schema: vol.Schema, suggested_values: dict[str, Any]
    ) -> vol.Schema:
        return data_schema

    @staticmethod
    def async_show_form(**kwargs: Any) -> dict[str, Any]:
        return {"type": "form", **kwargs}

    @staticmethod
    def async_create_entry(**kwargs: Any) -> dict[str, Any]:
        return {"type": "create_entry", **kwargs}

    @staticmethod
    def async_abort(**kwargs: Any) -> dict[str, Any]:
        return {"type": "abort", **kwargs}

    @staticmethod
    def async_update_reload_and_abort(
        entry: _ConfigEntry, *, data_updates: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "type": "abort",
            "reason": "reauth_successful",
            "entry": entry,
            "data_updates": data_updates,
        }


class _OptionsFlow:
    @staticmethod
    def async_create_entry(**kwargs: Any) -> dict[str, Any]:
        return {"type": "create_entry", **kwargs}

    @staticmethod
    def async_show_form(**kwargs: Any) -> dict[str, Any]:
        return {"type": "form", **kwargs}


class _ConfigEntries:
    @staticmethod
    def async_entries(_domain: str) -> list[Any]:
        return []


class _Hass:
    config = types.SimpleNamespace(
        country="RO", location_name="Home", path=lambda *_args: "/tmp"
    )
    config_entries = _ConfigEntries()

    async def async_add_executor_job(
        self, target: Any, *args: Any
    ) -> Any:
        return target(*args)


class _Store:
    """Minimal Home Assistant Store stub with inspectable constructor flags."""

    def __class_getitem__(cls, _item: Any) -> type[_Store]:
        return cls

    def __init__(
        self,
        hass: Any,
        version: int,
        key: str,
        **kwargs: Any,
    ) -> None:
        self.hass = hass
        self.version = version
        self.key = key
        self.kwargs = kwargs
        self.data: Any = None
        self.removed = False

    async def async_load(self) -> Any:
        return self.data

    async def async_save(self, data: Any) -> None:
        self.data = data

    async def async_remove(self) -> None:
        self.removed = True
        self.data = None


def _install_homeassistant_stubs() -> None:
    homeassistant = types.ModuleType("homeassistant")
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = _ConfigEntry
    config_entries.ConfigFlow = _ConfigFlow
    config_entries.ConfigFlowResult = dict
    config_entries.OptionsFlow = _OptionsFlow

    const = types.ModuleType("homeassistant.const")
    const.CONF_COUNTRY = "country"
    const.CONF_NAME = "name"
    const.CONF_PASSWORD = "password"
    const.CONF_USERNAME = "username"

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = _Hass

    def async_redact_data(value: Any, keys: set[str]) -> Any:
        if isinstance(value, dict):
            return {
                key: "<redacted>"
                if key in keys
                else async_redact_data(item, keys)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [async_redact_data(item, keys) for item in value]
        return value

    components = types.ModuleType("homeassistant.components")
    components_diagnostics = types.ModuleType(
        "homeassistant.components.diagnostics"
    )
    components_diagnostics.async_redact_data = async_redact_data

    selector = types.ModuleType("homeassistant.helpers.selector")
    selector.BooleanSelector = _Selector
    selector.CountrySelector = _Selector
    selector.NumberSelector = _Selector
    selector.NumberSelectorConfig = _SelectorConfig
    selector.NumberSelectorMode = types.SimpleNamespace(BOX="box")
    selector.TextSelector = _Selector
    selector.TextSelectorConfig = _SelectorConfig
    selector.TextSelectorType = types.SimpleNamespace(TEXT="text", PASSWORD="password")

    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda _hass: None
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.aiohttp_client = aiohttp_client
    helpers.selector = selector
    helpers_typing = types.ModuleType("homeassistant.helpers.typing")
    helpers_typing.VolDictType = dict
    helpers_storage = types.ModuleType("homeassistant.helpers.storage")
    helpers_storage.Store = _Store

    util = types.ModuleType("homeassistant.util")
    util.slugify = lambda value: re.sub(r"[^a-z0-9]+", "_", value.lower()).strip(
        "_"
    )

    modules = {
        "homeassistant": homeassistant,
        "homeassistant.components": components,
        "homeassistant.components.diagnostics": components_diagnostics,
        "homeassistant.config_entries": config_entries,
        "homeassistant.const": const,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.aiohttp_client": aiohttp_client,
        "homeassistant.helpers.selector": selector,
        "homeassistant.helpers.storage": helpers_storage,
        "homeassistant.helpers.typing": helpers_typing,
        "homeassistant.util": util,
    }
    for name, module in modules.items():
        sys.modules.setdefault(name, module)


_install_homeassistant_stubs()

custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(PACKAGE_PATH.parent)]
sys.modules.setdefault("custom_components", custom_components)

ecovacs_goat_g1 = types.ModuleType("custom_components.ecovacs_goat_g1")
ecovacs_goat_g1.__path__ = [str(PACKAGE_PATH)]
ecovacs_goat_g1.EcovacsConfigEntry = _ConfigEntry
sys.modules.setdefault("custom_components.ecovacs_goat_g1", ecovacs_goat_g1)
sys.modules["custom_components.ecovacs_goat_g1"].EcovacsConfigEntry = _ConfigEntry

from custom_components.ecovacs_goat_g1 import config_flow
from custom_components.ecovacs_goat_g1 import diagnostics as diagnostics_module
from custom_components.ecovacs_goat_g1 import util as util_module
from custom_components.ecovacs_goat_g1.const import (
    CONF_DEVICE_ID,
    CONF_SESSION_STORE_ID,
    CONF_VERIFICATION_CODE,
    DOMAIN,
)
from custom_components.ecovacs_goat_g1.mower_api import (
    AccountSession,
    EcovacsApiError,
    EcovacsDeviceVerificationRequiredError,
    EcovacsInvalidAuthError,
)
from custom_components.ecovacs_goat_g1.session_store import (
    AccountSessionStore,
    async_remove_account_session_store,
)
from custom_components.ecovacs_goat_g1.util import (
    generate_client_device_id,
    is_valid_client_device_id,
    is_valid_session_store_id,
    migrate_config_data,
    migrate_client_device_id,
)

PERSISTED_STORE_ID = "0123456789abcdef0123456789abcdef"
SENTINEL_SESSION = AccountSession(
    "sentinel-user-id", "sentinel-access-token"
)


def _api() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        account_session=SENTINEL_SESSION,
        set_account_session_update_callback=MagicMock(),
        request_device_verification_code=AsyncMock(),
        verify_device=AsyncMock(),
        get_devices=AsyncMock(return_value=[object()]),
    )


def _store() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        async_load=AsyncMock(return_value=None),
        async_save=AsyncMock(),
        async_save_verified=AsyncMock(),
    )


def _new_flow() -> config_flow.EcovacsConfigFlow:
    flow = config_flow.EcovacsConfigFlow()
    flow.hass = _Hass()
    return flow


def test_setup_1013_requests_otp_and_persists_same_device_id() -> None:
    """A setup challenge transitions to OTP without ever persisting the code."""
    flow = _new_flow()
    api = _api()
    store = _store()
    user_data = {
        "name": "Garden GOAT",
        "username": "owner@example.com",
        "password": "password",
        "country": "RO",
    }

    with (
        patch.object(config_flow, "_create_api", return_value=(api, store)),
        patch.object(
            config_flow,
            "_authenticate_and_find_devices",
            AsyncMock(side_effect=EcovacsDeviceVerificationRequiredError()),
        ),
    ):
        result = asyncio.run(flow.async_step_user(user_data))

    assert result["type"] == "form"
    assert result["step_id"] == "device_verification"
    api.request_device_verification_code.assert_awaited_once()
    device_id = flow._verification_data[CONF_DEVICE_ID]
    assert is_valid_client_device_id(device_id)
    result = asyncio.run(
        flow.async_step_device_verification({CONF_VERIFICATION_CODE: "123456"})
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_DEVICE_ID] == device_id
    assert CONF_VERIFICATION_CODE not in result["data"]
    api.verify_device.assert_awaited_once_with("123456")
    api.get_devices.assert_awaited_once_with()
    store.async_save.assert_awaited_once_with(None)
    store.async_save_verified.assert_awaited_once_with(SENTINEL_SESSION)


def test_reauth_sends_email_only_after_confirmation_and_reuses_stored_id() -> None:
    """Repair is user-confirmed and keeps the already registered client id."""
    persisted_id = "A1B2C3D4"
    entry = _ConfigEntry(
        {
            "name": "Garden GOAT",
            "username": "owner@example.com",
            "password": "password",
            "country": "RO",
            CONF_DEVICE_ID: persisted_id,
            CONF_SESSION_STORE_ID: PERSISTED_STORE_ID,
        }
    )
    flow = _new_flow()
    flow._test_reauth_entry = entry
    api = _api()
    store = _store()

    result = asyncio.run(flow.async_step_reauth(dict(entry.data)))
    assert result["step_id"] == "reauth_confirm"
    api.request_device_verification_code.assert_not_awaited()

    with (
        patch.object(
            config_flow, "_create_api", return_value=(api, store)
        ) as create_api,
        patch.object(
            config_flow,
            "_authenticate_and_find_devices",
            AsyncMock(side_effect=EcovacsDeviceVerificationRequiredError()),
        ),
    ):
        result = asyncio.run(
            flow.async_step_reauth_confirm({"password": "new-password"})
        )

    assert result["step_id"] == "device_verification"
    assert create_api.call_args.args[1][CONF_DEVICE_ID] == persisted_id
    assert create_api.call_args.args[1]["password"] == "new-password"
    api.request_device_verification_code.assert_awaited_once()

    result = asyncio.run(
        flow.async_step_device_verification({CONF_VERIFICATION_CODE: "654321"})
    )
    assert result["reason"] == "reauth_successful"
    assert result["data_updates"][CONF_DEVICE_ID] == persisted_id
    assert result["data_updates"]["password"] == "new-password"
    assert CONF_VERIFICATION_CODE not in result["data_updates"]
    store.async_save.assert_awaited_once_with(None)
    store.async_save_verified.assert_awaited_once_with(SENTINEL_SESSION)


def test_reauth_changed_password_can_succeed_without_sending_otp() -> None:
    """A corrected password is persisted when normal login now succeeds."""
    entry = _ConfigEntry(
        {
            "name": "Garden GOAT",
            "username": "owner@example.com",
            "password": "old-password",
            "country": "RO",
            CONF_DEVICE_ID: "A1B2C3D4",
            CONF_SESSION_STORE_ID: PERSISTED_STORE_ID,
        }
    )
    flow = _new_flow()
    flow._test_reauth_entry = entry
    api = _api()
    store = _store()
    asyncio.run(flow.async_step_reauth(dict(entry.data)))

    with (
        patch.object(config_flow, "_create_api", return_value=(api, store)),
        patch.object(
            config_flow,
            "_authenticate_and_find_devices",
            AsyncMock(return_value=True),
        ),
    ):
        result = asyncio.run(
            flow.async_step_reauth_confirm({"password": "correct-password"})
        )

    assert result["reason"] == "reauth_successful"
    assert result["data_updates"]["password"] == "correct-password"
    assert result["data_updates"][CONF_DEVICE_ID] == "A1B2C3D4"
    api.request_device_verification_code.assert_not_awaited()
    store.async_save_verified.assert_awaited_once_with(SENTINEL_SESSION)


def test_reauth_invalid_changed_password_stays_on_form() -> None:
    """A bad replacement password is rejected without sending an email code."""
    entry = _ConfigEntry(
        {
            "name": "Garden GOAT",
            "username": "owner@example.com",
            "password": "saved-password",
            "country": "RO",
            CONF_DEVICE_ID: "A1B2C3D4",
            CONF_SESSION_STORE_ID: PERSISTED_STORE_ID,
        }
    )
    flow = _new_flow()
    flow._test_reauth_entry = entry
    api = _api()
    store = _store()
    asyncio.run(flow.async_step_reauth(dict(entry.data)))

    with (
        patch.object(config_flow, "_create_api", return_value=(api, store)),
        patch.object(
            config_flow,
            "_authenticate_and_find_devices",
            AsyncMock(side_effect=EcovacsInvalidAuthError()),
        ),
    ):
        result = asyncio.run(
            flow.async_step_reauth_confirm({"password": "wrong-password"})
        )

    assert result["type"] == "form"
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"]["base"] == "invalid_auth"
    api.request_device_verification_code.assert_not_awaited()
    store.async_save.assert_awaited_once_with(None)


def test_verification_persists_session_before_mower_discovery() -> None:
    """A consumed OTP is durable before downstream GOAT discovery begins."""
    flow = _new_flow()
    api = _api()
    store = _store()
    flow._verification_api = api
    flow._verification_data = {
        "name": "Garden GOAT",
        "username": "owner@example.com",
        "password": "password",
        "country": "RO",
        CONF_DEVICE_ID: "A1B2C3D4",
        CONF_SESSION_STORE_ID: PERSISTED_STORE_ID,
    }
    flow._verification_store = store

    events: list[str] = []

    async def verify(_code: str) -> None:
        events.append("verify")
        await store.async_save_verified(SENTINEL_SESSION)

    async def discover() -> list[object]:
        events.append("discover")
        return [object()]

    async def save(_session: AccountSession | None) -> None:
        events.append("save")

    api.verify_device.side_effect = verify
    api.get_devices.side_effect = discover
    store.async_save_verified.side_effect = save

    result = asyncio.run(
        flow.async_step_device_verification(
            {CONF_VERIFICATION_CODE: "sentinel-otp"}
        )
    )

    assert result["type"] == "create_entry"
    assert events == ["verify", "save", "discover", "save"]


def test_transient_post_otp_failure_retries_without_resubmitting_otp() -> None:
    """A durable verified session binds the entry without consuming a new OTP."""
    flow = _new_flow()
    api = _api()
    store = _store()
    api.get_devices.side_effect = EcovacsApiError("transient")
    flow._verification_api = api
    flow._verification_data = {
        "name": "Garden GOAT",
        "username": "owner@example.com",
        "password": "password",
        "country": "RO",
        CONF_DEVICE_ID: "A1B2C3D4",
        CONF_SESSION_STORE_ID: PERSISTED_STORE_ID,
    }
    flow._verification_store = store

    result = asyncio.run(
        flow.async_step_device_verification(
            {CONF_VERIFICATION_CODE: "sentinel-otp"}
        )
    )

    assert result["type"] == "create_entry"
    api.verify_device.assert_awaited_once_with("sentinel-otp")
    api.get_devices.assert_awaited_once_with()
    store.async_save_verified.assert_awaited_once_with(SENTINEL_SESSION)


def test_no_devices_removes_verified_session() -> None:
    """An account without a GOAT cannot retain the just-verified raw session."""
    flow = _new_flow()
    api = _api()
    store = _store()
    api.get_devices.return_value = []

    async def verify(_code: str) -> None:
        await store.async_save_verified(SENTINEL_SESSION)

    api.verify_device.side_effect = verify
    flow._verification_api = api
    flow._verification_data = {
        "name": "Garden GOAT",
        "username": "owner@example.com",
        "password": "password",
        "country": "RO",
        CONF_DEVICE_ID: "A1B2C3D4",
        CONF_SESSION_STORE_ID: PERSISTED_STORE_ID,
    }
    flow._verification_store = store

    result = asyncio.run(
        flow.async_step_device_verification(
            {CONF_VERIFICATION_CODE: "sentinel-otp"}
        )
    )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "no_devices"
    store.async_save_verified.assert_awaited_once_with(SENTINEL_SESSION)
    store.async_save.assert_awaited_once_with(None)


def test_create_api_persists_seeded_session_updates_privately() -> None:
    """A loaded session persists rotations/clears until password fallback."""
    data = {
        "name": "Garden GOAT",
        "username": "owner@example.com",
        "password": "password",
        "country": "RO",
        CONF_DEVICE_ID: "A1B2C3D4",
        CONF_SESSION_STORE_ID: PERSISTED_STORE_ID,
    }
    store = MagicMock()
    store.async_load = AsyncMock(return_value=SENTINEL_SESSION)
    store.async_save = AsyncMock()
    api = MagicMock()

    with (
        patch.object(
            config_flow, "AccountSessionStore", return_value=store
        ) as store_class,
        patch.object(config_flow, "EcovacsMowerApi", return_value=api) as api_class,
    ):
        created_api, created_store = asyncio.run(
            config_flow._create_api(_Hass(), data)
        )

    assert created_api is api
    assert created_store is store
    store_class.assert_called_once()
    store_args = store_class.call_args.args
    assert isinstance(store_args[0], _Hass)
    assert store_args[1:] == (
        PERSISTED_STORE_ID,
        "A1B2C3D4",
        "owner@example.com",
        "RO",
    )
    assert api_class.call_args.kwargs["account_session"] == SENTINEL_SESSION
    seeded_callback = api_class.call_args.kwargs[
        "account_session_update_callback"
    ]
    assert seeded_callback is not None
    rotated = AccountSession("sentinel-rotated-user", "sentinel-rotated-token")
    replacement = AccountSession(
        "sentinel-replacement-user", "sentinel-replacement-token"
    )
    asyncio.run(seeded_callback(rotated))
    asyncio.run(seeded_callback(None))
    asyncio.run(seeded_callback(replacement))
    assert store.async_save.await_args_list == [call(rotated), call(None)]

    store.async_load = AsyncMock(return_value=None)
    store.async_save.reset_mock()
    with (
        patch.object(config_flow, "AccountSessionStore", return_value=store),
        patch.object(config_flow, "EcovacsMowerApi", return_value=api) as api_class,
    ):
        asyncio.run(config_flow._create_api(_Hass(), data))

    assert api_class.call_args.kwargs["account_session"] is None
    assert api_class.call_args.kwargs["account_session_update_callback"] is None


def test_verified_session_persistence_uses_write_readback_boundary() -> None:
    """OTP sessions use verified persistence while invalidation removes them."""
    api = MagicMock()
    store = _store()

    config_flow._enable_verified_session_persistence(api, store)
    callback = api.set_account_session_update_callback.call_args.args[0]
    asyncio.run(callback(SENTINEL_SESSION))
    asyncio.run(callback(None))

    store.async_save_verified.assert_awaited_once_with(SENTINEL_SESSION)
    store.async_save.assert_awaited_once_with(None)


def test_private_store_is_atomic_private_and_device_bound() -> None:
    """Raw material is private, atomic, and bound to account/country/device."""
    session_store = AccountSessionStore(
        _Hass(),
        PERSISTED_STORE_ID,
        "A1B2C3D4",
        "owner@example.com",
        "RO",
    )
    raw_store = session_store._store

    assert raw_store.kwargs == {"private": True, "atomic_writes": True}
    assert raw_store.key.endswith(f"auth_{PERSISTED_STORE_ID}")

    asyncio.run(session_store.async_save(SENTINEL_SESSION))
    assert raw_store.data["client_device_id"] == "A1B2C3D4"
    assert raw_store.data["user_id"] == SENTINEL_SESSION.user_id
    assert raw_store.data["access_token"] == SENTINEL_SESSION.access_token
    assert re.fullmatch(r"[0-9a-f]{64}", raw_store.data["account_fingerprint"])
    assert "owner@example.com" not in raw_store.data.values()
    saved_data = dict(raw_store.data)
    assert asyncio.run(session_store.async_load()) == SENTINEL_SESSION

    asyncio.run(session_store.async_save_verified(SENTINEL_SESSION))
    assert asyncio.run(session_store.async_load()) == SENTINEL_SESSION

    for username, country, device_id in (
        ("other@example.com", "RO", "A1B2C3D4"),
        ("owner@example.com", "DE", "A1B2C3D4"),
        ("owner@example.com", "RO", "Z9Y8X7W6"),
    ):
        mismatched = AccountSessionStore(
            _Hass(),
            PERSISTED_STORE_ID,
            device_id,
            username,
            country,
        )
        mismatched._store.data = dict(saved_data)
        assert asyncio.run(mismatched.async_load()) is None
        assert mismatched._store.removed is True


def test_private_store_ignores_malformed_partial_data_and_removes_cleanly() -> None:
    """Partial credentials never become an AccountSession."""
    session_store = AccountSessionStore(
        _Hass(),
        PERSISTED_STORE_ID,
        "A1B2C3D4",
        "owner@example.com",
        "RO",
    )
    raw_store = session_store._store

    for malformed in (
        None,
        [],
        {},
        {"client_device_id": "A1B2C3D4"},
        {
            "client_device_id": "A1B2C3D4",
            "user_id": "sentinel-user-id",
        },
        {
            "client_device_id": "A1B2C3D4",
            "user_id": "",
            "access_token": "sentinel-access-token",
        },
    ):
        raw_store.data = malformed
        assert asyncio.run(session_store.async_load()) is None

    asyncio.run(session_store.async_save(None))
    assert raw_store.removed is True
    assert raw_store.data is None


def test_remove_store_cleans_only_matching_corrupt_siblings(
    tmp_path: Path,
) -> None:
    """Entry removal cleans validated remnants without broad filesystem scope."""
    storage_dir = tmp_path / ".storage" / DOMAIN
    storage_dir.mkdir(parents=True)
    matching = [
        storage_dir / f"auth_{PERSISTED_STORE_ID}.corrupt.2026-07-19",
        storage_dir / f"auth_{PERSISTED_STORE_ID}.corrupt.second",
    ]
    for path in matching:
        path.write_text("sentinel", encoding="utf-8")
    unrelated = storage_dir / "auth_other.corrupt.2026-07-19"
    unrelated.write_text("keep", encoding="utf-8")
    matching_directory = (
        storage_dir / f"auth_{PERSISTED_STORE_ID}.corrupt.directory"
    )
    matching_directory.mkdir()

    hass = _Hass()
    hass.config = types.SimpleNamespace(
        path=lambda *parts: str(tmp_path.joinpath(*parts))
    )
    asyncio.run(
        async_remove_account_session_store(hass, PERSISTED_STORE_ID)
    )

    assert all(not path.exists() for path in matching)
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert matching_directory.is_dir()


def test_generated_client_device_id_has_vendor_format() -> None:
    """Fresh ids are exactly eight uppercase ASCII letters or digits."""
    generated = {generate_client_device_id() for _ in range(64)}

    assert len(generated) > 1
    assert all(re.fullmatch(r"[A-Z0-9]{8}", device_id) for device_id in generated)


def test_migration_replaces_v1_and_v2_nonconforming_ids_once() -> None:
    """Legacy and v2 ids are replaced once, then the replacement is stable."""
    for old_device_id in (None, "HA-home", "HA-0123456789abcdef"):
        original = {
            "name": "Garden GOAT",
            CONF_DEVICE_ID: old_device_id,
        }
        with patch.object(
            util_module,
            "generate_client_device_id",
            return_value="Z9Y8X7W6",
        ) as generate:
            migrated = migrate_client_device_id(original)
            migrated_again = migrate_client_device_id(migrated)

        assert migrated[CONF_DEVICE_ID] == "Z9Y8X7W6"
        assert migrated_again[CONF_DEVICE_ID] == "Z9Y8X7W6"
        assert migrated["name"] == original["name"]
        assert original[CONF_DEVICE_ID] == old_device_id
        generate.assert_called_once_with()


def test_migration_preserves_valid_client_device_id() -> None:
    """A conforming persisted id survives migration and every reload."""
    original = {
        "name": "Garden GOAT",
        CONF_DEVICE_ID: "A1B2C3D4",
    }
    with patch.object(util_module, "generate_client_device_id") as generate:
        migrated = migrate_client_device_id(original)
        migrated_again = migrate_client_device_id(migrated)

    assert migrated[CONF_DEVICE_ID] == "A1B2C3D4"
    assert migrated_again[CONF_DEVICE_ID] == "A1B2C3D4"
    generate.assert_not_called()


def test_v4_migration_adds_opaque_store_id_once_and_preserves_device_id() -> None:
    """Migration adds only stable non-secret identifiers to config-entry data."""
    original = {
        "name": "Garden GOAT",
        CONF_DEVICE_ID: "A1B2C3D4",
    }

    migrated = migrate_config_data(original)
    migrated_again = migrate_config_data(migrated)

    assert migrated[CONF_DEVICE_ID] == "A1B2C3D4"
    assert is_valid_session_store_id(migrated[CONF_SESSION_STORE_ID])
    assert migrated_again == migrated
    assert CONF_SESSION_STORE_ID not in original
    assert "access_token" not in migrated
    assert "user_id" not in migrated


def test_diagnostics_redact_config_and_device_identifiers() -> None:
    """Diagnostics never expose credentials or private-store lookup material."""
    entry = _ConfigEntry(
        {
            "name": "Sentinel GOAT",
            "username": "sentinel-owner@example.com",
            "password": "sentinel-password",
            "country": "RO",
            CONF_DEVICE_ID: "A1B2C3D4",
            CONF_SESSION_STORE_ID: PERSISTED_STORE_ID,
        },
        title="Sentinel entry title",
    )
    entry.runtime_data = types.SimpleNamespace(
        devices=[
            {
                "did": "sentinel-device-id",
                "name": "Sentinel device name",
                "homeId": "sentinel-home-id",
            }
        ],
        coordinators=[],
        debug_capture=types.SimpleNamespace(
            summary=lambda: {"active": None},
            recent_events=lambda *, limit: [
                {"data": {"accessToken": "<redacted>"}, "limit": limit}
            ],
        ),
    )

    diagnostics = asyncio.run(
        diagnostics_module.async_get_config_entry_diagnostics(_Hass(), entry)
    )
    rendered = json.dumps(diagnostics)

    for sensitive in (
        "sentinel-owner@example.com",
        "sentinel-password",
        "A1B2C3D4",
        PERSISTED_STORE_ID,
        "Sentinel entry title",
        "sentinel-device-id",
        "Sentinel device name",
        "sentinel-home-id",
    ):
        assert sensitive not in rendered
    assert rendered.count("<redacted>") >= 8
