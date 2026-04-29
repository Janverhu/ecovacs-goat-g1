"""Parse ECOVACS GOAT mower responses and MQTT pushes."""

from __future__ import annotations

import base64
import binascii
from dataclasses import replace
import json
import lzma
from typing import Any

from .mower_models import (
    MapPosition,
    MowerActivity,
    MowerMap,
    MowerMapInfo,
    MowerMapTrace,
    MowerSettings,
    MowerState,
    MowerStats,
    NetworkInfo,
)

MOWING_EFFICIENCY_OPTIONS = ("quick", "delicate")
MOWING_EFFICIENCY_BY_LEVEL = {1: "quick", 2: "delicate"}
MOWING_EFFICIENCY_LEVELS = {value: key for key, value in MOWING_EFFICIENCY_BY_LEVEL.items()}

OBSTACLE_AVOIDANCE_OPTIONS = ("short_grass", "general", "bumpy_tall_grass")
OBSTACLE_AVOIDANCE_BY_LEVEL = {
    1: "short_grass",
    2: "general",
    3: "bumpy_tall_grass",
}
OBSTACLE_AVOIDANCE_LEVELS = {
    value: key for key, value in OBSTACLE_AVOIDANCE_BY_LEVEL.items()
}
ERROR_DESCRIPTIONS = {
    0: "NoError: Robot is operational",
    100: "NoError: Robot is operational",
    422: "Weak signal, back to station",
    4200: "Robot not reachable",
    500: "Request Timeout",
}
RETURN_TO_STATION_ERROR_CODES = {422}
POSITION_HISTORY_ACTIVITIES = {
    MowerActivity.MOWING,
    MowerActivity.RETURNING,
}


def decode_payload(payload: str | bytes | bytearray | dict[str, Any]) -> dict[str, Any]:
    """Decode a JSON MQTT/HTTP payload into a dictionary."""
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode()
    return json.loads(payload)


def command_payload(data: Any) -> dict[str, Any]:
    """Return the app-style command envelope for an N-GIoT request."""
    return {
        "body": {"data": data},
        "header": {
            "pri": 2,
            "ts": None,
            "tzm": None,
            "ver": "0.0.22",
        },
    }


def normalise_time(value: str | None) -> str | None:
    """Normalise ECOVACS time strings such as 19:0 to 19:00."""
    if not value:
        return value
    hour, minute = str(value).split(":", 1)
    return f"{int(hour):02d}:{int(minute):02d}"


def body_data(message: dict[str, Any]) -> Any:
    """Extract body.data from a response/push payload."""
    body = message.get("body", message)
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def response_data(response: dict[str, Any]) -> Any:
    """Extract data from an N-GIoT response."""
    if response.get("ret") == "ok" and "resp" in response:
        return body_data(decode_payload(response["resp"]))
    return body_data(response)


def apply_response(state: MowerState, command: str, response: dict[str, Any]) -> MowerState:
    """Apply an HTTP command response to cached state."""
    data = response_data(response)
    return apply_command_data(state, command, data)


def apply_mqtt_payload(state: MowerState, topic: str, payload: str | bytes | bytearray) -> MowerState:
    """Apply an MQTT push payload to cached state."""
    command = topic.split("/")[2] if "/" in topic else topic
    message = decode_payload(payload)
    data = body_data(message)
    if isinstance(data, dict):
        data = {**data, "_mqtt_ts": (message.get("header") or {}).get("ts")}
    return apply_command_data(state, command, data)


def apply_command_data(state: MowerState, command: str, data: Any) -> MowerState:
    """Apply command data from grouped reads, direct reads, or pushes."""
    if command == "getInfo" and isinstance(data, dict):
        for nested_command, nested in data.items():
            nested_data = nested.get("data", nested) if isinstance(nested, dict) else nested
            state = apply_command_data(state, nested_command, nested_data)
        return state

    match command:
        case "getBattery" | "onBattery":
            if isinstance(data, dict):
                state = replace(state, battery=_int(data.get("value")))
        case "getChargeState" | "onChargeState":
            if isinstance(data, dict):
                state = replace(
                    state,
                    charging=_bool(data.get("isCharging")),
                    charge_mode=data.get("mode"),
                    activity=MowerActivity.DOCKED
                    if _bool(data.get("isCharging"))
                    else state.activity,
                )
        case "getCleanInfo_V2" | "onCleanInfo_V2" | "getCleanInfo" | "onCleanInfo":
            if isinstance(data, dict):
                activity = _clean_activity(data, state.activity)
                if (
                    activity is MowerActivity.PAUSED
                    and (
                        state.activity is MowerActivity.RETURNING
                        or state.error_code in RETURN_TO_STATION_ERROR_CODES
                    )
                ):
                    activity = MowerActivity.RETURNING
                mower_map = state.map
                if (
                    activity is MowerActivity.MOWING
                    and state.activity
                    not in {
                        MowerActivity.UNKNOWN,
                        MowerActivity.MOWING,
                        MowerActivity.PAUSED,
                    }
                ):
                    mower_map = replace(mower_map, position_history=())
                state = replace(
                    state,
                    activity=activity,
                    charging=False if activity is MowerActivity.MOWING else state.charging,
                    task_id=_task_id(data, state.task_id),
                    map=mower_map,
                )
        case "onWorkState" | "getWorkState":
            if isinstance(data, dict):
                activity = _work_state_activity(data, state.activity)
                mower_map = state.map
                if (
                    activity is MowerActivity.MOWING
                    and state.activity
                    not in {
                        MowerActivity.UNKNOWN,
                        MowerActivity.MOWING,
                        MowerActivity.PAUSED,
                    }
                ):
                    mower_map = replace(mower_map, position_history=())
                state = replace(
                    state,
                    activity=activity,
                    charging=False if activity is MowerActivity.MOWING else state.charging,
                    map=mower_map,
                )
        case "getStats" | "onStats" | "reportStats":
            if isinstance(data, dict):
                mowed_area = _int(data.get("mowedArea"))
                job_area = _int(data.get("area"))
                state = replace(
                    state,
                    task_id=_task_id(data, state.task_id),
                    stats=replace(
                        state.stats,
                        area=mowed_area if mowed_area is not None else job_area,
                        job_area=job_area,
                        progress=_progress(data, mowed_area, job_area),
                        duration=_int(data.get("time")),
                    ),
                )
        case "getLastTimeStats" | "onLastTimeStats":
            if isinstance(data, dict):
                state = replace(state, task_id=_task_id(data, state.task_id))
        case "getTotalStats":
            if isinstance(data, dict):
                state = replace(
                    state,
                    stats=replace(
                        state.stats,
                        total_area=_int(data.get("area")),
                        total_duration=_int(data.get("time")),
                        total_count=_int(data.get("count")),
                    ),
                )
        case "getError" | "onError":
            if isinstance(data, dict):
                codes = data.get("code")
                code = codes[-1] if isinstance(codes, list) and codes else _int(codes)
                state = replace(
                    state,
                    error_code=code,
                    error_description=ERROR_DESCRIPTIONS.get(code or 0),
                    activity=_error_activity(code, state),
                )
        case "getPos" | "onPos":
            if isinstance(data, dict):
                state = replace(
                    state,
                    map=_map_position_data(
                        state.map,
                        data,
                        record_history=state.activity in POSITION_HISTORY_ACTIVITIES,
                    ),
                )
        case "getUWB" | "onUWB":
            if isinstance(data, dict):
                state = replace(state, map=_map_uwb_data(state.map, data))
        case "getMapTrace_V2" | "onMapTrace_V2":
            if isinstance(data, dict):
                state = replace(state, map=_map_trace_data(state.map, data))
        case "getMapInfo_V2" | "onMapInfo_V2":
            if isinstance(data, dict):
                state = replace(state, map=_map_info_data(state.map, data))
        case "getWifiList" | "onWifiList":
            if isinstance(data, dict):
                first = next(iter(data.get("list", []) or []), {})
                state = replace(
                    state,
                    network=NetworkInfo(
                        ip=first.get("ip"),
                        ssid=first.get("ssid"),
                        rssi=_int(first.get("rssi")),
                        mac=data.get("mac"),
                    ),
                )
        case "getLifeSpan":
            if isinstance(data, list):
                lifespans = dict(state.lifespans)
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    left = _float(item.get("left"))
                    total = _float(item.get("total"))
                    if item.get("type") and left is not None and total and total > 0:
                        lifespans[str(item["type"])] = round(left / total * 100, 2)
                state = replace(state, lifespans=lifespans)
        case "getRainDelay" | "onRainDelay":
            if isinstance(data, dict):
                state = replace(
                    state,
                    settings=replace(
                        state.settings,
                        rain_enabled=_bool(data.get("enable")),
                        rain_delay=_int(data.get("delay")),
                    ),
                )
        case "getAnimProtect" | "onAnimProtect":
            if isinstance(data, dict):
                state = replace(
                    state,
                    settings=replace(
                        state.settings,
                        animal_enabled=_bool(data.get("enable")),
                        animal_start=normalise_time(data.get("start")),
                        animal_end=normalise_time(data.get("end")),
                    ),
                )
        case "getRecognization" | "onRecognization":
            if isinstance(data, dict):
                state = replace(
                    state,
                    settings=replace(
                        state.settings,
                        ai_recognition=_bool(data.get("state")),
                    ),
                )
        case "getBorderSwitch" | "onBorderSwitch":
            if isinstance(data, dict):
                state = replace(
                    state,
                    settings=replace(
                        state.settings,
                        border_switch=_bool(data.get("enable")),
                        border_mode=_int(data.get("mode")),
                    ),
                )
        case "getChildLock" | "onChildLock":
            if isinstance(data, dict):
                state = replace(
                    state,
                    settings=replace(state.settings, safer_mode=_bool(data.get("on"))),
                )
        case "getMoveupWarning" | "onMoveupWarning":
            if isinstance(data, dict):
                state = replace(
                    state,
                    settings=replace(
                        state.settings,
                        move_up_warning=_bool(data.get("enable")),
                    ),
                )
        case "getCrossMapBorderWarning" | "onCrossMapBorderWarning":
            if isinstance(data, dict):
                state = replace(
                    state,
                    settings=replace(
                        state.settings,
                        cross_map_border_warning=_bool(data.get("enable")),
                    ),
                )
        case "getCutDirection" | "onCutDirection":
            if isinstance(data, dict):
                state = replace(
                    state,
                    settings=replace(
                        state.settings,
                        cut_direction=_int(data.get("angle")),
                    ),
                )
        case "getCutEfficiency" | "onCutEfficiency":
            if isinstance(data, dict):
                level = _int(data.get("level"))
                state = replace(
                    state,
                    settings=replace(
                        state.settings,
                        mowing_efficiency=MOWING_EFFICIENCY_BY_LEVEL.get(level or 0),
                    ),
                )
        case "getObstacleHeight" | "onObstacleHeight":
            if isinstance(data, dict):
                level = _int(data.get("level"))
                state = replace(
                    state,
                    settings=replace(
                        state.settings,
                        obstacle_avoidance=OBSTACLE_AVOIDANCE_BY_LEVEL.get(level or 0),
                    ),
                )
        case "onProtectState" | "getProtectState":
            if isinstance(data, dict):
                state = replace(
                    state,
                    settings=replace(
                        state.settings,
                        animal_enabled=_bool(data.get("isAnimProtect"))
                        if data.get("isAnimProtect") is not None
                        else state.settings.animal_enabled,
                        safer_mode=_bool(data.get("isLocked"))
                        if data.get("isLocked") is not None
                        else state.settings.safer_mode,
                    ),
                )
        case "getRobotFeature" | "onRobotFeature":
            if isinstance(data, dict):
                state = replace(state, robot_features=dict(data))

    raw = dict(state.raw)
    raw[command] = data
    return replace(state, raw=raw, available=True)


def _clean_activity(data: dict[str, Any], current: MowerActivity) -> MowerActivity:
    state = data.get("state")
    clean_state = data.get("cleanState") or {}
    motion_state = clean_state.get("motionState")
    if data.get("trigger") == "alert":
        return MowerActivity.ERROR
    if motion_state == "pause" or data.get("paused") == 1:
        return MowerActivity.PAUSED
    if state == "goCharging" or motion_state == "goCharging":
        return MowerActivity.RETURNING
    if state in ("clean", "washing") or motion_state == "working":
        return MowerActivity.MOWING
    if state == "idle":
        if current is MowerActivity.DOCKED:
            return MowerActivity.DOCKED
        return MowerActivity.IDLE
    return current


def _task_id(data: dict[str, Any], current: str | None) -> str | None:
    """Return the best current mowing task id found in app payloads."""
    for key in ("bdTaskID", "mowid", "cid", "cleanId"):
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return current


def _work_state_activity(data: dict[str, Any], current: MowerActivity) -> MowerActivity:
    robot_state = (data.get("robotState") or {}).get("state")
    station_state = (data.get("stationState") or {}).get("state")
    if data.get("paused") == 1:
        return MowerActivity.PAUSED
    if robot_state == "cleaning":
        return MowerActivity.MOWING
    if station_state in ("goCharging", "goEmptying"):
        return MowerActivity.RETURNING
    if station_state in ("charging", "emptying", "washing", "drying"):
        return MowerActivity.DOCKED
    if robot_state == "idle" and station_state == "idle":
        return MowerActivity.IDLE
    return current


def _error_activity(code: int | None, state: MowerState) -> MowerActivity:
    """Return activity implied by an error payload."""
    if code in (None, 0, 100):
        return state.activity
    if code in RETURN_TO_STATION_ERROR_CODES:
        if state.charging is True or state.activity is MowerActivity.DOCKED:
            return MowerActivity.DOCKED
        return MowerActivity.RETURNING
    return MowerActivity.ERROR


def _map_position_data(
    current: MowerMap, data: dict[str, Any], *, record_history: bool
) -> MowerMap:
    """Merge mower, station, and beacon positions into the map cache."""
    mower_position = _map_position(data.get("deebotPos"))
    charge_positions = _map_positions(data.get("chargePos"))
    uwb_positions = _map_positions(data.get("uwbPos"))
    history = current.position_history

    if record_history and mower_position and mower_position.invalid != 1:
        if not history or (
            history[-1].x != mower_position.x or history[-1].y != mower_position.y
        ):
            history = (*history, mower_position)

    return replace(
        current,
        mid=str(data.get("mid")) if data.get("mid") is not None else current.mid,
        current_position=mower_position or current.current_position,
        charge_positions=charge_positions or current.charge_positions,
        uwb_positions=uwb_positions or current.uwb_positions,
        position_history=history,
        last_update_ts=_int(data.get("_mqtt_ts")) or current.last_update_ts,
        revision=current.revision + 1,
    )

def _map_uwb_data(current: MowerMap, data: dict[str, Any]) -> MowerMap:
    """Merge beacon position data from getUWB/onUWB payloads."""
    uwb_positions = _map_positions(data.get("uwbPos"))
    if uwb_positions and not any(
        position.x != 0 or position.y != 0 for position in uwb_positions
    ):
        uwb_positions = ()

    return replace(
        current,
        mid=str(data.get("mid")) if data.get("mid") is not None else current.mid,
        uwb_positions=uwb_positions or current.uwb_positions,
        last_update_ts=_int(data.get("_mqtt_ts")) or current.last_update_ts,
    )


def _map_trace_data(current: MowerMap, data: dict[str, Any]) -> MowerMap:
    """Merge chunked onMapTrace_V2 data into the map cache."""
    batch_id = str(data.get("batid")) if data.get("batid") is not None else None
    serial = str(data.get("serial")) if data.get("serial") is not None else None
    trace_type = str(data.get("type")) if data.get("type") is not None else None
    index = _int(data.get("index"))
    info = data.get("info")

    trace = current.trace
    if (
        batch_id
        and (
            trace.batch_id != batch_id
            or trace.serial != serial
            or trace.type != trace_type
        )
    ):
        trace = MowerMapTrace(batch_id=batch_id, serial=serial, type=trace_type)

    chunks = dict(trace.chunks)
    if index is not None and isinstance(info, str):
        chunks[index] = info
    path = _decode_trace_path(chunks) or trace.path

    return replace(
        current,
        mid=str(data.get("mid")) if data.get("mid") is not None else current.mid,
        trace=replace(
            trace,
            batch_id=batch_id or trace.batch_id,
            serial=serial or trace.serial,
            info_size=_int(data.get("infoSize")) or trace.info_size,
            type=trace_type or trace.type,
            chunks=chunks,
            path=path,
        ),
        last_update_ts=_int(data.get("_mqtt_ts")) or current.last_update_ts,
        revision=current.revision + 1,
    )


def _map_info_data(current: MowerMap, data: dict[str, Any]) -> MowerMap:
    """Merge chunked onMapInfo_V2 data into the base map cache."""
    batch_id = str(data.get("batid")) if data.get("batid") is not None else None
    serial = str(data.get("serial")) if data.get("serial") is not None else None
    map_type = str(data.get("type")) if data.get("type") is not None else None
    index = _int(data.get("index"))
    info = data.get("info")

    map_info = current.info
    if (
        batch_id
        and (
            map_info.batch_id != batch_id
            or map_info.serial != serial
            or map_info.type != map_type
        )
    ):
        map_info = MowerMapInfo(batch_id=batch_id, serial=serial, type=map_type)

    chunks = dict(map_info.chunks)
    if index is not None and isinstance(info, str):
        chunks[index] = info

    outline, obstacles = _decode_base_map(chunks)

    return replace(
        current,
        mid=str(data.get("mid")) if data.get("mid") is not None else current.mid,
        info=replace(
            map_info,
            batch_id=batch_id or map_info.batch_id,
            serial=serial or map_info.serial,
            info_size=_int(data.get("infoSize")) or map_info.info_size,
            type=map_type or map_info.type,
            chunks=chunks,
            outline=outline or map_info.outline,
            obstacles=obstacles or map_info.obstacles,
        ),
        last_update_ts=_int(data.get("_mqtt_ts")) or current.last_update_ts,
    )


def _map_position(data: Any) -> MapPosition | None:
    if not isinstance(data, dict):
        return None
    return MapPosition.from_payload(data)


def _map_positions(data: Any) -> tuple[MapPosition, ...]:
    if not isinstance(data, list):
        return ()
    return tuple(
        position
        for item in data
        if (position := _map_position(item)) is not None and position.invalid != 1
    )


def _decode_trace_path(chunks: dict[int, str]) -> tuple[MapPosition, ...]:
    """Decode ECOVACS' chunked LZMA-wrapped live trace path."""
    try:
        payload = _decode_lzma_json_chunks(chunks)
    except (binascii.Error, ValueError, lzma.LZMAError, json.JSONDecodeError):
        return ()

    positions: list[MapPosition] = []
    if not isinstance(payload, list):
        return ()
    for item in payload:
        if not isinstance(item, list) or len(item) < 2 or not isinstance(item[1], str):
            continue
        for coordinates in item[1].split(";")[1:]:
            if "," not in coordinates:
                continue
            x_value, y_value, *_ = coordinates.split(",")
            try:
                positions.append(MapPosition(x=int(x_value), y=int(y_value)))
            except ValueError:
                continue
    return tuple(positions)


def _decode_base_map(
    chunks: dict[int, str],
) -> tuple[tuple[MapPosition, ...], tuple[tuple[MapPosition, ...], ...]]:
    """Decode ECOVACS' base map into lawn outline and obstacle polygons."""
    try:
        payload = _decode_lzma_json_chunks(chunks)
    except (binascii.Error, ValueError, lzma.LZMAError, json.JSONDecodeError):
        return (), ()

    if not isinstance(payload, list):
        return (), ()

    outline_candidates: list[tuple[MapPosition, ...]] = []
    obstacles: list[tuple[MapPosition, ...]] = []

    for item in payload:
        if not isinstance(item, list) or not item:
            continue
        layer = str(item[0])
        if layer in {"1", "2"} and len(item) > 1 and isinstance(item[1], str):
            positions = _positions_from_coordinate_string(item[1])
            if positions:
                outline_candidates.append(positions)
        elif layer == "3":
            for obstacle_data in item[1:]:
                if isinstance(obstacle_data, str):
                    obstacle = _positions_from_coordinate_string(obstacle_data)
                    if len(obstacle) >= 3:
                        obstacles.append(obstacle)

    outline = max(outline_candidates, key=len, default=())
    return outline, tuple(obstacles)


def _decode_lzma_json_chunks(chunks: dict[int, str]) -> Any:
    """Decode ECOVACS' compact LZMA chunk wrapper into JSON."""
    if not chunks:
        raise ValueError("No chunks")
    indexes = sorted(chunks)
    if indexes != list(range(indexes[-1] + 1)):
        raise ValueError("Incomplete chunks")
    raw = b"".join(base64.b64decode(chunks[index]) for index in indexes)
    if len(raw) < 10:
        raise ValueError("Chunk payload too small")
    props = raw[0]
    lc = props % 9
    remainder = props // 9
    lp = remainder % 5
    pb = remainder // 5
    decompressor = lzma.LZMADecompressor(
        format=lzma.FORMAT_RAW,
        filters=[
            {
                "id": lzma.FILTER_LZMA1,
                "dict_size": int.from_bytes(raw[1:5], "little"),
                "lc": lc,
                "lp": lp,
                "pb": pb,
            }
        ],
    )
    decoded = decompressor.decompress(
        raw[9:], max_length=int.from_bytes(raw[5:9], "little")
    )
    return json.loads(decoded)


def _positions_from_coordinate_string(value: str) -> tuple[MapPosition, ...]:
    """Parse semicolon-delimited ECOVACS map coordinates."""
    positions: list[MapPosition] = []
    for coordinates in value.split(";")[1:]:
        parts = coordinates.split(",")
        if len(parts) < 2:
            continue
        try:
            positions.append(MapPosition(x=int(parts[0]), y=int(parts[1])))
        except ValueError:
            continue
    return tuple(positions)


def _bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(int(value)) if isinstance(value, str | int | float) else bool(value)


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _progress(
    data: dict[str, Any], mowed_area: int | None, job_area: int | None
) -> float | None:
    """Return current job mowing progress as a percentage."""
    for key in ("progress", "cleanProgress", "mowingProgress", "percent", "percentage"):
        value = _float(data.get(key))
        if value is not None:
            if 0 <= value <= 1:
                value *= 100
            return round(max(0, min(100, value)), 1)
    if mowed_area is None or job_area is None or job_area <= 0:
        return None
    return round(max(0, min(100, mowed_area / job_area * 100)), 1)


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
