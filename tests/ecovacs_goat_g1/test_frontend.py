"""Tests for bundled Lovelace card registration."""

from __future__ import annotations

from pathlib import Path
import sys
import types

import pytest

PACKAGE_PATH = Path(__file__).parents[2] / "custom_components" / "ecovacs_goat_g1"

custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(PACKAGE_PATH.parent)]
sys.modules.setdefault("custom_components", custom_components)

ecovacs_goat_g1 = types.ModuleType("custom_components.ecovacs_goat_g1")
ecovacs_goat_g1.__path__ = [str(PACKAGE_PATH)]
sys.modules.setdefault("custom_components.ecovacs_goat_g1", ecovacs_goat_g1)


def _install_homeassistant_stubs() -> types.ModuleType:
    """Provide the Home Assistant imports frontend.py needs when HA is absent."""
    if "homeassistant.components.frontend" in sys.modules:
        return sys.modules["homeassistant.components.frontend"]

    extra_js_urls: list[str] = []

    def add_extra_js_url(_hass: object, url: str) -> None:
        extra_js_urls.append(url)

    class StaticPathConfig:
        def __init__(self, url_path: str, path: str, cache_headers: bool) -> None:
            self.url_path = url_path
            self.path = path
            self.cache_headers = cache_headers

    modules = {
        "homeassistant": types.ModuleType("homeassistant"),
        "homeassistant.components": types.ModuleType("homeassistant.components"),
        "homeassistant.components.frontend": types.ModuleType(
            "homeassistant.components.frontend"
        ),
        "homeassistant.components.http": types.ModuleType("homeassistant.components.http"),
        "homeassistant.components.lovelace": types.ModuleType(
            "homeassistant.components.lovelace"
        ),
        "homeassistant.components.lovelace.const": types.ModuleType(
            "homeassistant.components.lovelace.const"
        ),
        "homeassistant.core": types.ModuleType("homeassistant.core"),
        "homeassistant.loader": types.ModuleType("homeassistant.loader"),
    }
    for name, module in modules.items():
        module.__path__ = []
        sys.modules[name] = module

    frontend = modules["homeassistant.components.frontend"]
    frontend.add_extra_js_url = add_extra_js_url
    frontend.extra_js_urls = extra_js_urls
    modules["homeassistant.components.http"].StaticPathConfig = StaticPathConfig
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.components.lovelace.const"].LOVELACE_DATA = "lovelace"
    modules["homeassistant.components.lovelace.const"].MODE_STORAGE = "storage"

    async def async_get_integration(_hass: object, _domain: str) -> object:
        raise AssertionError("integration version fallback should not run")

    modules["homeassistant.loader"].async_get_integration = async_get_integration
    return frontend


_FRONTEND_STUB = _install_homeassistant_stubs()

from custom_components.ecovacs_goat_g1.frontend import (  # noqa: E402
    CARD_URL_PATH,
    async_register_frontend_card,
)


class FakeHass:
    """Minimal hass stand-in for card registration."""

    def __init__(self) -> None:
        self.data: dict = {}
        self.static_calls: list = []
        self.static_error: Exception | None = None
        self.http = self

    async def async_register_static_paths(self, configs: list) -> None:
        if self.static_error is not None:
            raise self.static_error
        self.static_calls.append(configs)

    async def async_add_executor_job(self, target, *args):
        return target(*args)


class FakeResources:
    """In-memory Lovelace resource collection."""

    def __init__(self, items: list[dict] | None = None) -> None:
        self.items = list(items or [])
        self.calls: list[str] = []
        self.created: list[dict] = []
        self.updated: list[tuple] = []

    async def async_get_info(self) -> dict[str, int]:
        self.calls.append("info")
        return {"resources": len(self.items)}

    def async_items(self) -> list[dict]:
        self.calls.append("items")
        assert self.calls[-2] == "info"
        return list(self.items)

    async def async_create_item(self, data: dict) -> dict:
        self.calls.append("create")
        self.created.append(data)
        item = {"id": f"created-{len(self.items)}", "type": "module", "url": data["url"]}
        self.items.append(item)
        return item

    async def async_update_item(self, item_id: str, updates: dict) -> dict:
        self.calls.append("update")
        self.updated.append((item_id, updates))
        for item in self.items:
            if item.get("id") == item_id:
                item.update(updates)
        return updates


class LovelaceData:
    def __init__(self, resource_mode: str, resources: FakeResources) -> None:
        self.resource_mode = resource_mode
        self.resources = resources


def _card_url() -> str:
    import hashlib

    card_path = PACKAGE_PATH / "frontend" / "ecovacs-goat-card.js"
    token = hashlib.sha256(card_path.read_bytes()).hexdigest()[:12]
    return f"{CARD_URL_PATH}?v={token}"


@pytest.fixture
def extra_js_urls() -> list[str]:
    _FRONTEND_STUB.extra_js_urls.clear()
    return _FRONTEND_STUB.extra_js_urls


@pytest.fixture
def hass() -> FakeHass:
    return FakeHass()


@pytest.mark.asyncio
async def test_storage_mode_creates_one_module_resource(hass: FakeHass, extra_js_urls: list[str]) -> None:
    """Storage mode creates the module resource and does not use extra JS."""
    legacy = {
        "id": "legacy",
        "type": "module",
        "url": "/local/ecovacs_goat/ecovacs-goat-card.js",
    }
    resources = FakeResources([legacy])
    hass.data["lovelace"] = LovelaceData("storage", resources)

    await async_register_frontend_card(hass)

    url = _card_url()
    assert resources.created == [{"res_type": "module", "url": url}]
    assert resources.updated == []
    assert legacy in resources.items
    assert extra_js_urls == []
    assert hass.static_calls
    config = hass.static_calls[0][0]
    assert config.url_path == CARD_URL_PATH
    assert config.cache_headers is False
    assert config.path.endswith("ecovacs-goat-card.js")

    await async_register_frontend_card(hass)
    assert resources.created == [{"res_type": "module", "url": url}]
    assert extra_js_urls == []


@pytest.mark.asyncio
async def test_storage_mode_updates_query_and_skips_current(
    hass: FakeHass, extra_js_urls: list[str]
) -> None:
    """A changed content hash updates the existing card resource only."""
    url = _card_url()
    resources = FakeResources(
        [
            {"id": "stale", "type": "module", "url": f"{CARD_URL_PATH}?v=oldhash"},
            {"id": "current", "type": "module", "url": url},
            {
                "id": "hacs",
                "type": "module",
                "url": "/hacsfiles/ecovacs_goat_g1/frontend/ecovacs-goat-card.js",
            },
        ]
    )
    hass.data["lovelace"] = LovelaceData("storage", resources)

    await async_register_frontend_card(hass)

    assert resources.created == []
    assert resources.updated == [("stale", {"url": url})]
    assert resources.items[2]["url"].startswith("/hacsfiles/")
    assert extra_js_urls == []


@pytest.mark.asyncio
async def test_storage_mode_matches_path_without_query(
    hass: FakeHass, extra_js_urls: list[str]
) -> None:
    """An unversioned bundled-card resource is updated to the hashed URL."""
    url = _card_url()
    resources = FakeResources(
        [{"id": "plain", "type": "js", "url": CARD_URL_PATH}]
    )
    hass.data["lovelace"] = LovelaceData("storage", resources)

    await async_register_frontend_card(hass)

    assert resources.created == []
    assert resources.updated == [("plain", {"url": url})]
    assert extra_js_urls == []


@pytest.mark.asyncio
async def test_yaml_mode_uses_extra_js_url(hass: FakeHass, extra_js_urls: list[str]) -> None:
    """YAML resources are not writable, so the card stays an extra JS module."""

    class YamlLovelace:
        resource_mode = "yaml"

        @property
        def resources(self):
            raise AssertionError("yaml mode must not read resources")

    hass.data["lovelace"] = YamlLovelace()

    await async_register_frontend_card(hass)

    assert extra_js_urls == [_card_url()]
    assert hass.static_calls


@pytest.mark.asyncio
async def test_missing_lovelace_data_uses_extra_js_url(
    hass: FakeHass, extra_js_urls: list[str]
) -> None:
    """Without Lovelace data, registration falls back to the extra JS URL."""
    await async_register_frontend_card(hass)

    assert extra_js_urls == [_card_url()]


@pytest.mark.asyncio
async def test_storage_failure_falls_back_to_extra_js_url(
    hass: FakeHass, extra_js_urls: list[str]
) -> None:
    """A storage error still loads the card, and a later setup does not retry."""

    class BrokenResources(FakeResources):
        async def async_get_info(self) -> dict[str, int]:
            raise RuntimeError("storage unavailable")

    resources = BrokenResources()
    hass.data["lovelace"] = LovelaceData("storage", resources)

    await async_register_frontend_card(hass)
    await async_register_frontend_card(hass)

    assert extra_js_urls == [_card_url()]
    assert resources.created == []


@pytest.mark.asyncio
async def test_static_path_already_registered_still_adds_resource(
    hass: FakeHass, extra_js_urls: list[str]
) -> None:
    """A duplicate static-path registration does not skip the Lovelace resource."""
    hass.static_error = RuntimeError("already registered")
    resources = FakeResources()
    hass.data["lovelace"] = LovelaceData("storage", resources)

    await async_register_frontend_card(hass)

    assert resources.created == [{"res_type": "module", "url": _card_url()}]
    assert extra_js_urls == []
