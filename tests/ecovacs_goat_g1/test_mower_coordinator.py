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


def _rtk_lawn_pushes(
    batch: str, chain: str, mid: str = MID
) -> list[tuple[str, dict]]:
    pieces = _info_pieces([["1", f"s1;1;0,0;{chain}"], ["2", "1"]])
    return [
        (
            "onMI",
            {"mid": mid, "batid": batch, "serial": len(pieces), "type": "0",
             "using": 1, "index": index, "info": info},
        )
        for index, info in enumerate(pieces)
    ]


def _o800_trace_pushes(
    batch: str, points: list[tuple[int, int]]
) -> list[tuple[str, dict]]:
    pieces = _info_pieces([["1", "-1662072064;"], ["2"], ["3", _coordinates(points)]])
    return [
        (
            "onMapTrace",
            {"mid": "123456789", "batid": batch, "serial": len(pieces), "type": "0",
             "index": index, "info": info},
        )
        for index, info in enumerate(pieces)
    ]


def _position(heading: int) -> tuple[str, dict]:
    return (
        "onPos",
        {
            "deebotPos": {"x": 120, "y": 180, "a": heading, "invalid": 0},
            "chargePos": [{"x": 0, "y": 0, "a": 0, "t": 1, "invalid": 1}],
            "mid": "0",
        },
    )


def _push(coordinator: MowerCoordinator, command: str, data: dict) -> None:
    message = json.dumps({"header": {"ts": 1}, "body": {"data": data}})
    coordinator._handle_mqtt_message(
        f"iot/atr/{command}/did/class/resource/j", message.encode()
    )


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
        self.payloads: list[tuple[str, Any]] = []
        self.getinfo: dict[str, dict] = {}

    async def control(self, _device: MowerDevice, command: str, payload: Any) -> dict:
        self.commands.append(command)
        self.payloads.append((command, payload))
        assert self.coordinator is not None
        for push_command, data in self.pushes.get(command, ()):
            _push(self.coordinator, push_command, data)
        await asyncio.sleep(0)
        if command == "getPos":
            return GET_POS_REPLY
        if command == "getInfo":
            return {
                "body": {
                    "data": {
                        name: self.getinfo[name] for name in payload if name in self.getinfo
                    }
                }
            }
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


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["GOAT O800 RTK", "GOAT O1200 LiDAR Pro"])
async def test_o_series_lawn_survives_positions_with_another_map_id(model: str) -> None:
    """onMI says mid 1 while onPos says mid 0, so onMI must not own the map id.

    Otherwise every position push looks like a remap and clears the lawn.
    """
    api = FakeApi()
    coordinator = _coordinator(api, MowerState(), model=model)

    for command, data in (
        _position(0),
        *_rtk_lawn_pushes("lawn", RTK_LAWN_CHAIN, mid="1"),
        _position(0),
    ):
        _push(coordinator, command, data)
    await asyncio.sleep(0)

    assert coordinator.data.map.mid == "0"
    assert _points(coordinator.data.map.info.outline) == RTK_LAWN
    await coordinator.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "trace_pushes"),
    [("GOAT G1-800", _trace_pushes), ("GOAT O800 RTK", _o800_trace_pushes)],
)
async def test_mowed_path_pushes_wait_for_a_turn(
    model: str,
    trace_pushes: Any,
) -> None:
    """After the first path, a path push is applied once the mower has turned."""
    api = FakeApi()
    coordinator = _coordinator(
        api,
        MowerState(activity=MowerActivity.MOWING, map=MowerMap(mid="0")),
        model=model,
    )
    first = TRACE[:2]

    for command, data in trace_pushes("first", first):
        _push(coordinator, command, data)
    await asyncio.sleep(0)
    assert _points(coordinator.data.map.trace.path) == first

    for command, data in trace_pushes("straight", TRACE):
        _push(coordinator, command, data)
    await asyncio.sleep(0)
    assert _points(coordinator.data.map.trace.path) == first

    for command, data in (
        _position(0),
        _position(120),
        *trace_pushes("turned", TRACE),
    ):
        _push(coordinator, command, data)
    await asyncio.sleep(0)
    assert _points(coordinator.data.map.trace.path) == TRACE
    await coordinator.async_stop()


@pytest.mark.asyncio
async def test_edge_trim_sends_borderrotate_until_it_ends() -> None:
    """A trim is paused, resumed and stopped by type; the next mow is auto again."""
    api = FakeApi()
    coordinator = _coordinator(
        api,
        MowerState(activity=MowerActivity.DOCKED),
        model="GOAT O1200 LiDAR Pro",
    )
    coordinator._last_readback_at = mower_coordinator.monotonic()

    await coordinator.start_edge_trim()
    assert coordinator.data.activity is MowerActivity.MOWING
    assert coordinator.data.task_type == "borderrotate"
    await coordinator.pause()
    await coordinator.start_mowing()
    await coordinator.end_mowing()
    assert coordinator.data.task_type is None
    await coordinator.start_mowing()

    assert [body for command, body in api.payloads if command == "clean"] == [
        {"act": "start", "content": {"type": "borderrotate", "value": "reid:1"}},
        {"act": "pause", "content": {"type": "borderrotate"}},
        {"act": "resume", "content": {"type": "borderrotate"}},
        {"act": "stop", "content": {"type": "borderrotate"}},
        {"act": "start", "content": {"type": "auto"}},
    ]
    assert coordinator.data.task_type == "auto"
    await coordinator.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["GOAT O800 RTK", "GOAT O1200 LiDAR Pro"])
async def test_o_series_readback_keeps_a_scheduled_mow_running(model: str) -> None:
    """getCleanInfo says idle during a scheduled mow; getScheduleTaskInfo does not."""
    api = FakeApi()
    api.getinfo = {
        "getCleanInfo": {"code": 0, "msg": "ok", "data": {"state": "idle"}},
        "getScheduleTaskInfo": {
            "code": 0,
            "msg": "ok",
            "data": {
                "trigger": "schedule",
                "state": "clean",
                "cleanState": {
                    "motionState": "working",
                    "content": {"type": "spotArea", "value": "1"},
                },
            },
        },
    }
    coordinator = _coordinator(api, MowerState(activity=MowerActivity.DOCKED), model=model)

    await coordinator.async_refresh_state()
    await coordinator.async_refresh_state()

    group = next(
        body for command, body in api.payloads
        if command == "getInfo" and "getCleanInfo" in body
    )
    assert group.index("getScheduleTaskInfo") == group.index("getCleanInfo") + 1
    assert coordinator.data.activity is MowerActivity.MOWING
    assert coordinator.data.task_type == "spotArea"
    await coordinator.async_stop()


@pytest.mark.asyncio
async def test_g1_readback_does_not_ask_for_schedule_task_info() -> None:
    api = FakeApi()
    coordinator = _coordinator(api, MowerState())

    await coordinator.async_refresh_state()

    requested = [
        name for command, body in api.payloads if command == "getInfo" for name in body
    ]
    assert "getCleanInfo_V2" in requested
    assert "getScheduleTaskInfo" not in requested
    await coordinator.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "trace", "kept"),
    [
        ("GOAT G1-800", (MapPosition(x=1, y=1),), 800),
        ("GOAT O1200 LiDAR Pro", (), 1001),
    ],
)
async def test_live_path_is_trimmed_only_when_the_mower_sends_a_trace(
    model: str, trace: tuple[MapPosition, ...], kept: int
) -> None:
    """Without a mower trace, the positions are the whole mowed path."""
    mowing = MowerState(
        activity=MowerActivity.MOWING,
        map=MowerMap(
            mid="0",
            current_position=MapPosition(x=5000, y=0),
            position_history=tuple(MapPosition(x=x, y=0) for x in range(1000)),
            trace=MowerMapTrace(path=trace),
        ),
    )
    coordinator = _coordinator(FakeApi(), mowing, model=model)

    compact = coordinator._compact_live_position_segment(mowing)

    assert len(compact.map.position_history) == kept
    assert compact.map.position_history[-1] == MapPosition(x=5000, y=0)
    await coordinator.async_stop()


@pytest.mark.asyncio
async def test_edge_trim_is_refused_without_a_trimmer() -> None:
    api = FakeApi()
    coordinator = _coordinator(api, MowerState(), model="GOAT O800 RTK")

    with pytest.raises(mower_coordinator.HomeAssistantError):
        await coordinator.start_edge_trim()

    assert api.commands == []
    await coordinator.async_stop()
