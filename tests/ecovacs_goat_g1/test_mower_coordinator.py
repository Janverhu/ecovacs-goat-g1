"""Tests for how coordinator refreshes merge replies with pushed map data."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import json
import lzma
from pathlib import Path
import sys
import types
from typing import Any, Generic, TypeVar
from unittest.mock import AsyncMock

import pytest

PACKAGE_PATH = Path(__file__).parents[2] / "custom_components" / "ecovacs_goat_g1"

custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(PACKAGE_PATH.parent)]
sys.modules.setdefault("custom_components", custom_components)

ecovacs_goat_g1 = types.ModuleType("custom_components.ecovacs_goat_g1")
ecovacs_goat_g1.__path__ = [str(PACKAGE_PATH)]
sys.modules.setdefault("custom_components.ecovacs_goat_g1", ecovacs_goat_g1)

_DataT = TypeVar("_DataT")


class _DataUpdateCoordinator(Generic[_DataT]):
    """The part of Home Assistant's coordinator the mower coordinator relies on."""

    def __init__(self, hass: Any, logger: Any, *, config_entry: Any, name: str) -> None:
        self.hass = hass
        self.logger = logger
        self.config_entry = config_entry
        self.name = name
        self.data: Any = None

    def async_set_updated_data(self, data: Any) -> None:
        self.data = data


class _Store:
    def __init__(self, *_args: Any) -> None:
        pass

    async def async_load(self) -> None:
        return None

    async def async_save(self, _data: Any) -> None:
        return None

    def async_delay_save(self, _data_func: Any, _delay: float = 0) -> None:
        return None


class _HomeAssistantError(Exception):
    pass


class _ConfigEntryAuthFailed(_HomeAssistantError):
    pass


class _UpdateFailed(Exception):
    pass


def _install_stubs() -> None:
    """Provide the Home Assistant and paho imports the coordinator needs when absent."""
    stubs: dict[str, dict[str, Any]] = {
        "homeassistant": {},
        "homeassistant.components": {},
        "homeassistant.components.persistent_notification": {
            "async_create": lambda *_args, **_kwargs: None,
            "async_dismiss": lambda *_args, **_kwargs: None,
        },
        "homeassistant.config_entries": {"ConfigEntry": object},
        "homeassistant.const": {"EVENT_HOMEASSISTANT_STOP": "homeassistant_stop"},
        "homeassistant.core": {"Event": object, "HomeAssistant": object},
        "homeassistant.exceptions": {
            "ConfigEntryAuthFailed": _ConfigEntryAuthFailed,
            "HomeAssistantError": _HomeAssistantError,
        },
        "homeassistant.helpers": {},
        "homeassistant.helpers.storage": {"Store": _Store},
        "homeassistant.helpers.update_coordinator": {
            "DataUpdateCoordinator": _DataUpdateCoordinator,
            "UpdateFailed": _UpdateFailed,
        },
    }
    try:
        import paho.mqtt.client  # noqa: F401
    except ModuleNotFoundError:
        stubs.update({"paho": {}, "paho.mqtt": {}, "paho.mqtt.client": {}})
    for name, attributes in stubs.items():
        module = sys.modules.get(name)
        if module is None:
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        for attribute, value in attributes.items():
            if not hasattr(module, attribute):
                setattr(module, attribute, value)


_install_stubs()

from custom_components.ecovacs_goat_g1 import mower_coordinator  # noqa: E402
from custom_components.ecovacs_goat_g1.mower_coordinator import (  # noqa: E402
    MowerCoordinator,
)
from custom_components.ecovacs_goat_g1.mower_models import (  # noqa: E402
    MapPosition,
    MowerActivity,
    MowerDevice,
    MowerMap,
    MowerMapInfo,
    MowerMapTrace,
    MowerState,
)

MID = "1813510333"
TRACE = [(100, 200), (300, 200), (300, 400), (100, 400)]
LAWN = [(0, 0), (900, 0), (900, 900), (0, 900)]
# O-series lawn: start point plus a 50-unit direction chain.
RTK_LAWN_CHAIN = "4(18)6(18)8(18)2(18)"
RTK_LAWN = [(0, 0), (900, 0), (900, -900), (0, -900), (0, 0)]
GET_POS_REPLY = {
    "body": {
        "data": {
            "deebotPos": {"x": 120, "y": 180, "a": 90, "invalid": 0},
            "chargePos": [{"x": 0, "y": 0, "a": 0}],
            "mid": MID,
        }
    }
}


def _info_pieces(layers: list[list[str]]) -> list[str]:
    """Return two ``info`` slices of one base64 + LZMA blob, as the mower sends."""
    raw = json.dumps(layers, separators=(",", ":")).encode()
    compressed = lzma.compress(
        raw,
        format=lzma.FORMAT_RAW,
        filters=[
            {"id": lzma.FILTER_LZMA1, "dict_size": 0x40000, "lc": 3, "lp": 0, "pb": 2}
        ],
    )
    header = bytes([0x5D]) + (0x40000).to_bytes(4, "little")
    blob = header + len(raw).to_bytes(4, "little") + compressed
    text = base64.b64encode(blob).decode()
    half = len(text) // 2
    return [text[:half], text[half:]]


def _coordinates(points: list[tuple[int, int]]) -> str:
    return "0;" + ";".join(f"{x},{y}" for x, y in points)


def _trace_pushes(batch: str, points: list[tuple[int, int]]) -> list[tuple[str, dict]]:
    pieces = _info_pieces([["0", _coordinates(points)]])
    return [
        (
            "onMapTrace_V2",
            {"mid": MID, "batid": batch, "serial": len(pieces), "type": "0",
             "index": index, "info": info},
        )
        for index, info in enumerate(pieces)
    ]


def _lawn_pushes(batch: str, points: list[tuple[int, int]]) -> list[tuple[str, dict]]:
    pieces = _info_pieces([["1", _coordinates(points)]])
    return [
        (
            "onMapInfo_V2",
            {"mid": MID, "batid": batch, "serial": len(pieces), "type": "0",
             "using": 1, "index": index, "info": info},
        )
        for index, info in enumerate(pieces)
    ]


def _rtk_lawn_pushes(batch: str, chain: str) -> list[tuple[str, dict]]:
    pieces = _info_pieces([["1", f"s1;1;0,0;{chain}"], ["2", "1"]])
    return [
        (
            "onMI",
            {"mid": MID, "batid": batch, "serial": len(pieces), "type": "0",
             "using": 1, "index": index, "info": info},
        )
        for index, info in enumerate(pieces)
    ]


def _points(path: tuple[MapPosition, ...]) -> list[tuple[int, int]]:
    return [(position.x, position.y) for position in path]


class FakeHass:
    """The hass attributes the coordinator touches outside setup."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.is_stopping = False

    def async_create_task(self, coro: Any) -> asyncio.Task:
        return self.loop.create_task(coro)

    def async_create_background_task(self, coro: Any, name: str) -> asyncio.Task:
        return self.loop.create_task(coro, name=name)


class FakeApi:
    """Answer mower commands the way the ECOVACS cloud does.

    A command listed in ``pushes`` delivers those MQTT pushes while its HTTP
    reply is still pending, which is when the mower sends map pieces.
    """

    reauth_required = False

    def __init__(self) -> None:
        self.coordinator: MowerCoordinator | None = None
        self.pushes: dict[str, list[tuple[str, dict]]] = {}
        self.commands: list[str] = []

    async def control(self, _device: MowerDevice, command: str, _payload: Any) -> dict:
        self.commands.append(command)
        assert self.coordinator is not None
        for push_command, data in self.pushes.get(command, ()):
            message = json.dumps({"header": {"ts": 1}, "body": {"data": data}})
            self.coordinator._handle_mqtt_message(
                f"iot/atr/{push_command}/did/class/resource/j", message.encode()
            )
        await asyncio.sleep(0)
        if command == "getPos":
            return GET_POS_REPLY
        return {"body": {"data": {}}}


def _restored_state() -> MowerState:
    """State after a restart: lawn and live segment from storage, no trace yet."""
    return MowerState(
        activity=MowerActivity.DOCKED,
        map=MowerMap(
            mid=MID,
            info=MowerMapInfo(
                outline=tuple(MapPosition(x=x, y=y) for x, y in LAWN)
            ),
            position_history=tuple(MapPosition(x=x, y=0) for x in range(13)),
        ),
    )


def _coordinator(
    api: FakeApi, state: MowerState, model: str = "GOAT G1-800"
) -> MowerCoordinator:
    device = MowerDevice(
        did="did", device_class="class", resource="resource", name="GOAT",
        model=model,
    )
    coordinator = MowerCoordinator(
        FakeHass(asyncio.get_running_loop()), api, device, object()
    )
    coordinator.data = state
    coordinator._mqtt = AsyncMock()
    coordinator._app_presence_mqtt = AsyncMock()
    api.coordinator = coordinator
    return coordinator


async def _run_one_keepalive_round(
    coordinator: MowerCoordinator, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mower_coordinator, "APP_PING_INTERVAL_SECONDS", 0)
    await coordinator.async_request_live_position_stream(
        "card_keepalive_button", force=True, duration_seconds=600
    )
    task = coordinator._live_position_keepalive_task
    assert task is not None
    await task


@pytest.mark.asyncio
async def test_keepalive_keeps_trace_pushed_while_its_requests_are_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The trace that answers the keepalive's getMapTrace_V2 stays on the map.

    Its pieces arrive before the keepalive's last reply. Writing back the state
    the keepalive started from erased them after every Home Assistant restart.
    """
    api = FakeApi()
    api.pushes["getMapTrace_V2"] = _trace_pushes("after_restart", TRACE)
    coordinator = _coordinator(api, _restored_state())

    await _run_one_keepalive_round(coordinator, monkeypatch)

    assert _points(coordinator.data.map.trace.path) == TRACE
    assert api.commands[-1] == "getMapPoint"
    await coordinator.async_stop()


@pytest.mark.asyncio
async def test_keepalive_applies_the_trace_it_requests_while_the_turn_gate_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A keepalive asks for the trace, so the reply replaces an older one."""
    api = FakeApi()
    api.pushes["getMapTrace_V2"] = _trace_pushes("newer", TRACE)
    state = _restored_state()
    state = replace(
        state,
        map=replace(
            state.map,
            trace=MowerMapTrace(batch_id="older", path=(MapPosition(x=1, y=1),)),
        ),
    )
    coordinator = _coordinator(api, state)
    coordinator._trace_update_due = False

    await _run_one_keepalive_round(coordinator, monkeypatch)

    assert _points(coordinator.data.map.trace.path) == TRACE
    await coordinator.async_stop()


@pytest.mark.asyncio
async def test_readback_keeps_lawn_pieces_pushed_during_its_trace_request() -> None:
    """Lawn pieces that follow getMapInfo_V2 survive the rest of the readback."""
    api = FakeApi()
    api.pushes["getMapTrace_V2"] = [
        *_lawn_pushes("lawn", LAWN),
        *_trace_pushes("trace", TRACE),
    ]
    coordinator = _coordinator(api, MowerState(map=MowerMap(mid=MID)))
    coordinator._device_mqtt_ready = True

    await coordinator.async_refresh_state()

    assert api.commands.index("getMapInfo_V2") < api.commands.index("getMapTrace_V2")
    assert _points(coordinator.data.map.info.outline) == LAWN
    assert _points(coordinator.data.map.trace.path) == TRACE
    await coordinator.async_stop()


@pytest.mark.asyncio
async def test_o_series_readback_keeps_lawn_pieces_pushed_during_later_requests() -> None:
    """The O-series lawn follows getMI while the map-set reads are still pending."""
    api = FakeApi()
    api.pushes["getMapTrack"] = _rtk_lawn_pushes("lawn", RTK_LAWN_CHAIN)
    coordinator = _coordinator(
        api, MowerState(map=MowerMap(mid=MID)), model="GOAT O800 RTK"
    )
    coordinator._device_mqtt_ready = True

    await coordinator.async_refresh_state()

    assert api.commands.index("getMI") < api.commands.index("getMapTrack")
    assert "getMapTrace_V2" not in api.commands
    assert _points(coordinator.data.map.info.outline) == RTK_LAWN
    await coordinator.async_stop()
