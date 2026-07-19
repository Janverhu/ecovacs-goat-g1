"""Tests for config-entry removal ordering."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, patch

PACKAGE_PATH = Path(__file__).parents[2] / "custom_components" / "ecovacs_goat_g1"
TEST_PACKAGE = "ecovacs_goat_g1_remove_test"
CONTROLLER_PACKAGE = "ecovacs_goat_g1_controller_stop_test"


class _GenericConfigEntry:
    @classmethod
    def __class_getitem__(cls, _item: Any) -> type[_GenericConfigEntry]:
        return cls


def _load_component_init(
    remove_store: AsyncMock,
) -> types.ModuleType:
    """Load the integration entry points with narrow Home Assistant stubs."""
    homeassistant = types.ModuleType("homeassistant")
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = _GenericConfigEntry

    const = types.ModuleType("homeassistant.const")
    const.ATTR_ENTITY_ID = "entity_id"
    const.Platform = types.SimpleNamespace(
        BUTTON="button",
        LAWN_MOWER="lawn_mower",
        NUMBER="number",
        SELECT="select",
        SENSOR="sensor",
        SWITCH="switch",
        TIME="time",
    )

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.ServiceCall = object
    core.SupportsResponse = types.SimpleNamespace(ONLY="only")

    exceptions = types.ModuleType("homeassistant.exceptions")
    exceptions.HomeAssistantError = RuntimeError

    config_validation = types.ModuleType(
        "homeassistant.helpers.config_validation"
    )
    config_validation.entity_ids = lambda value: value
    config_validation.string = lambda value: value
    config_validation.boolean = lambda value: value
    entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.config_validation = config_validation
    helpers.entity_registry = entity_registry

    package_const = types.ModuleType(f"{TEST_PACKAGE}.const")
    package_const.CONF_SESSION_STORE_ID = "session_store_id"
    package_const.DOMAIN = "ecovacs_goat_g1"
    for name in (
        "SERVICE_CLEAR_DEBUG_CAPTURE",
        "SERVICE_EXPORT_DEBUG_CAPTURE",
        "SERVICE_MARK_DEBUG_CAPTURE",
        "SERVICE_REFRESH_STATE",
        "SERVICE_REQUEST_LIVE_POSITION_STREAM",
        "SERVICE_START_DEBUG_CAPTURE",
        "SERVICE_STOP_DEBUG_CAPTURE",
    ):
        setattr(package_const, name, name.lower())

    package_controller = types.ModuleType(f"{TEST_PACKAGE}.controller")
    package_controller.EcovacsController = type("EcovacsController", (), {})
    package_frontend = types.ModuleType(f"{TEST_PACKAGE}.frontend")
    package_frontend.async_register_frontend_card = AsyncMock()
    package_session_store = types.ModuleType(f"{TEST_PACKAGE}.session_store")
    package_session_store.async_remove_account_session_store = remove_store
    package_util = types.ModuleType(f"{TEST_PACKAGE}.util")
    package_util.migrate_config_data = lambda data: dict(data)

    spec = importlib.util.spec_from_file_location(
        TEST_PACKAGE,
        PACKAGE_PATH / "__init__.py",
        submodule_search_locations=[str(PACKAGE_PATH)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    modules = {
        TEST_PACKAGE: module,
        f"{TEST_PACKAGE}.const": package_const,
        f"{TEST_PACKAGE}.controller": package_controller,
        f"{TEST_PACKAGE}.frontend": package_frontend,
        f"{TEST_PACKAGE}.session_store": package_session_store,
        f"{TEST_PACKAGE}.util": package_util,
        "homeassistant": homeassistant,
        "homeassistant.config_entries": config_entries,
        "homeassistant.const": const,
        "homeassistant.core": core,
        "homeassistant.exceptions": exceptions,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.config_validation": config_validation,
        "homeassistant.helpers.entity_registry": entity_registry,
    }
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


def _load_controller_module() -> types.ModuleType:
    """Load the controller with narrow dependency stubs."""
    parent = types.ModuleType(CONTROLLER_PACKAGE)
    parent.__path__ = [str(PACKAGE_PATH)]

    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    const = types.ModuleType("homeassistant.const")
    const.CONF_COUNTRY = "country"
    const.CONF_NAME = "name"
    const.CONF_PASSWORD = "password"
    const.CONF_USERNAME = "username"
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    exceptions = types.ModuleType("homeassistant.exceptions")
    exceptions.ConfigEntryAuthFailed = type(
        "ConfigEntryAuthFailed", (RuntimeError,), {}
    )
    exceptions.ConfigEntryNotReady = type(
        "ConfigEntryNotReady", (RuntimeError,), {}
    )
    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda _hass: None
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.aiohttp_client = aiohttp_client

    package_const = types.ModuleType(f"{CONTROLLER_PACKAGE}.const")
    for name, value in {
        "DEFAULT_DEBUG_CAPTURE_MAX_DURATION_MINUTES": 30,
        "DEFAULT_DEBUG_CAPTURE_MAX_SIZE_MB": 25,
        "DEFAULT_DEBUG_CAPTURE_RAW_PAYLOADS": False,
        "OPTION_DEBUG_CAPTURE_MAX_DURATION_MINUTES": "duration",
        "OPTION_DEBUG_CAPTURE_MAX_SIZE_MB": "size",
        "OPTION_DEBUG_CAPTURE_RAW_PAYLOADS": "raw",
    }.items():
        setattr(package_const, name, value)

    package_debug = types.ModuleType(f"{CONTROLLER_PACKAGE}.debug_capture")
    package_debug.DebugCaptureStore = type("DebugCaptureStore", (), {})
    package_api = types.ModuleType(f"{CONTROLLER_PACKAGE}.mower_api")
    package_api.AccountSession = type("AccountSession", (), {})
    package_api.EcovacsAuthError = type("EcovacsAuthError", (RuntimeError,), {})
    package_api.EcovacsDeviceVerificationRequiredError = type(
        "EcovacsDeviceVerificationRequiredError",
        (package_api.EcovacsAuthError,),
        {},
    )
    package_api.EcovacsInvalidAuthError = type(
        "EcovacsInvalidAuthError", (package_api.EcovacsAuthError,), {}
    )
    package_api.EcovacsMowerApi = type("EcovacsMowerApi", (), {})
    package_coordinator = types.ModuleType(
        f"{CONTROLLER_PACKAGE}.mower_coordinator"
    )
    package_coordinator.MowerCoordinator = type("MowerCoordinator", (), {})
    package_store = types.ModuleType(f"{CONTROLLER_PACKAGE}.session_store")
    package_store.AccountSessionStore = type("AccountSessionStore", (), {})
    package_util = types.ModuleType(f"{CONTROLLER_PACKAGE}.util")
    package_util.get_client_device_id = lambda _data: "A1B2C3D4"
    package_util.get_session_store_id = (
        lambda _data: "0123456789abcdef0123456789abcdef"
    )

    module_name = f"{CONTROLLER_PACKAGE}.controller"
    spec = importlib.util.spec_from_file_location(
        module_name, PACKAGE_PATH / "controller.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    modules = {
        CONTROLLER_PACKAGE: parent,
        module_name: module,
        f"{CONTROLLER_PACKAGE}.const": package_const,
        f"{CONTROLLER_PACKAGE}.debug_capture": package_debug,
        f"{CONTROLLER_PACKAGE}.mower_api": package_api,
        f"{CONTROLLER_PACKAGE}.mower_coordinator": package_coordinator,
        f"{CONTROLLER_PACKAGE}.session_store": package_store,
        f"{CONTROLLER_PACKAGE}.util": package_util,
        "homeassistant.config_entries": config_entries,
        "homeassistant.const": const,
        "homeassistant.core": core,
        "homeassistant.exceptions": exceptions,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.aiohttp_client": aiohttp_client,
    }
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


def test_remove_entry_tears_down_before_store_deletion_idempotently() -> None:
    """Late callbacks are tombstoned before private session data is deleted."""
    events: list[str] = []
    controller = types.SimpleNamespace(
        teardown=AsyncMock(side_effect=lambda: events.append("teardown"))
    )
    remove_store = AsyncMock(side_effect=lambda *_args: events.append("remove"))
    component = _load_component_init(remove_store)
    entry = types.SimpleNamespace(
        data={"session_store_id": "0123456789abcdef0123456789abcdef"},
        runtime_data=controller,
    )
    hass = object()

    asyncio.run(component.async_remove_entry(hass, entry))
    asyncio.run(component.async_remove_entry(hass, entry))

    assert events == ["teardown", "remove", "teardown", "remove"]
    assert controller.teardown.await_count == 2
    assert remove_store.await_count == 2


def test_remove_entry_without_runtime_still_deletes_store() -> None:
    """A never-loaded legacy entry can be removed without controller state."""
    remove_store = AsyncMock()
    component = _load_component_init(remove_store)
    entry = types.SimpleNamespace(
        data={"session_store_id": "0123456789abcdef0123456789abcdef"}
    )
    hass = object()

    asyncio.run(component.async_remove_entry(hass, entry))

    remove_store.assert_awaited_once_with(
        hass, "0123456789abcdef0123456789abcdef"
    )


def test_remove_entry_deletes_store_even_when_teardown_fails() -> None:
    """The private credential file is removed unconditionally."""
    events: list[str] = []

    async def fail_teardown() -> None:
        events.append("teardown")
        raise RuntimeError("sentinel teardown failure")

    controller = types.SimpleNamespace(teardown=AsyncMock(side_effect=fail_teardown))
    remove_store = AsyncMock(side_effect=lambda *_args: events.append("remove"))
    component = _load_component_init(remove_store)
    entry = types.SimpleNamespace(
        data={"session_store_id": "0123456789abcdef0123456789abcdef"},
        runtime_data=controller,
    )

    async def run() -> None:
        try:
            await component.async_remove_entry(object(), entry)
        except RuntimeError as err:
            assert str(err) == "sentinel teardown failure"
        else:
            raise AssertionError("teardown failure was swallowed")

    asyncio.run(run())

    assert events == ["teardown", "remove"]
    remove_store.assert_awaited_once()


def test_stop_coordinators_is_best_effort() -> None:
    """One failing coordinator cannot prevent the others from stopping."""
    component = _load_controller_module()
    controller = component.EcovacsController.__new__(
        component.EcovacsController
    )
    failing = types.SimpleNamespace(
        async_stop=AsyncMock(side_effect=RuntimeError("sentinel stop failure"))
    )
    succeeding = types.SimpleNamespace(async_stop=AsyncMock())

    with patch.object(component._LOGGER, "warning") as warning:
        asyncio.run(controller._stop_coordinators([failing, succeeding]))

    failing.async_stop.assert_awaited_once_with()
    succeeding.async_stop.assert_awaited_once_with()
    warning.assert_called_once()
