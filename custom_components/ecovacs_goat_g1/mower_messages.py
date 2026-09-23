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
# The live position/beacon stream reports the map the mower is physically in, so
# it is the single source of truth for the active map id. Only these commands
# may switch the active map (and reset stale geometry); base-map / trace replies
# merely contribute geometry for whichever map is already active.
_ACTIVE_MAP_ID_COMMANDS = {"getPos", "onPos", "getUWB", "onUWB"}


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

    if command in _ACTIVE_MAP_ID_COMMANDS:
        state = _reset_map_on_id_change(state, data)

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
        case "onFwBuryPoint-bd_basicinfo" | "onFwBuryPoint-bd_locationjump":
            # O1200 LiDAR Pro leaves getPos at the origin. The live point is
            # this telemetry string (x,y and a height/flag the map does not use).
            if isinstance(data, dict):
                state = _apply_telemetry_position(state, data)
        case "getUWB" | "onUWB":
            if isinstance(data, dict):
                state = replace(state, map=_map_uwb_data(state.map, data))
        case "getMapTrace_V2" | "onMapTrace_V2":
            if isinstance(data, dict):
                state = replace(state, map=_map_trace_data(state.map, data))
        case "getMapInfo_V2" | "onMapInfo_V2":
            if isinstance(data, dict):
                state = replace(state, map=_map_info_data(state.map, data))
        case "getMapTrack" | "onMapTrack" | "getAreaSet" | "onAreaSet":
            # O-series (RTK) map-set layers: virtual walls ("vw") and areas
            # ("ar"). The ``subsets`` field is base64 + the same compact-LZMA
            # wrapper used by the G1 V2 map, so it decodes with the shared
            # decoder.
            if isinstance(data, dict):
                state = replace(state, map=_map_set_layer(state.map, data))
        case (
            "getMapState"
            | "onMapState"
            | "getMI"
            | "onMI"
            | "getSpecialContour"
            | "onSpecialContour"
            | "getMapInfo"
            | "onMapInfo"
        ):
            # O-series (RTK) map dialect. The base-map / contour geometry for
            # these is delivered over MQTT, not in the HTTP reply, so we only
            # learn the map id here; the live marker comes from getPos/onPos
            # (deebotPos + rtkPos).
            if isinstance(data, dict):
                state = replace(state, map=_map_mid_only(state.map, data))
        case "getRTK" | "onRTK":
            # O-series RTK reference: the fixed base station position. There is
            # one station; show it where the G1 shows UWB beacons.
            if isinstance(data, dict):
                station = _rtk_station(data)
                if station is not None:
                    state = replace(
                        state, map=replace(state.map, rtk_station=station)
                    )
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
    trigger = data.get("trigger")
    if trigger == "alert":
        return MowerActivity.ERROR
    if motion_state == "pause" or data.get("paused") == 1:
        return MowerActivity.PAUSED
    if state == "goCharging" or motion_state == "goCharging":
        return MowerActivity.RETURNING
    if state in ("clean", "working", "washing") or motion_state == "working":
        return MowerActivity.MOWING
    # A scheduled job that fires on the mower (not started from HA) reports a
    # schedule trigger; treat an active scheduled job as mowing even when the
    # exact state token differs by model (see issue #7, O1200 scheduled tasks).
    if trigger in ("schedule", "appointment", "scheduleClean") and state not in (
        "idle",
        "goCharging",
    ):
        return MowerActivity.MOWING
    if state == "idle":
        if current is MowerActivity.DOCKED:
            return MowerActivity.DOCKED
        return MowerActivity.IDLE
    return current


def _task_id(data: dict[str, Any], current: str | None) -> str | None:
    """Return the best current mowing task id found in app payloads.

    O-series ``getCleanInfo`` nests the task id under ``cleanState.cid`` while G1
    stats readbacks expose it at the top level, so check both.
    """
    sources: list[dict[str, Any]] = [data]
    clean_state = data.get("cleanState")
    if isinstance(clean_state, dict):
        sources.append(clean_state)
    for source in sources:
        for key in ("bdTaskID", "mowid", "cid", "cleanId"):
            value = source.get(key)
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


def _unfixed_origin(position: MapPosition | None, data: dict[str, Any]) -> bool:
    """Return whether a getPos origin is the LiDAR placeholder, not a real fix.

    O1200 LiDAR Pro answers getPos with deebotPos at 0,0 and an empty rtkPos
    list while it is mowing. That placeholder must not pin the marker, and must
    not replace a telemetry point. A G1 or RTK reading that also carries beacons
    is left alone, including a real dock at the origin.
    """
    if position is None or position.x or position.y:
        return False
    return not _map_positions(data.get("uwbPos")) and not _map_positions(
        data.get("rtkPos")
    )


def _apply_telemetry_position(state: MowerState, data: dict[str, Any]) -> MowerState:
    """Apply an O1200 LiDAR telemetry point to the live marker."""
    points = [
        position
        for key in ("prev", "robotPos", "curr")
        if (position := _csv_position(data.get(key))) is not None
    ]
    charger = _csv_position(data.get("chargerPos"))
    if not points and charger is None:
        return state

    mower_map = state.map
    history = mower_map.position_history
    if state.activity in POSITION_HISTORY_ACTIVITIES:
        for position in points:
            if not history or history[-1].x != position.x or history[-1].y != position.y:
                history = (*history, position)
    return replace(
        state,
        map=replace(
            mower_map,
            current_position=points[-1] if points else mower_map.current_position,
            charge_positions=(charger,) if charger is not None else mower_map.charge_positions,
            position_history=history,
            revision=mower_map.revision + 1,
        ),
    )


def _csv_position(value: Any) -> MapPosition | None:
    """Parse ``x,y,...`` telemetry into a map point.

    The first two fields are the map coordinates. Later fields are height and
    flags. An all-zero point is the stationary placeholder and is ignored.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    parts = [part.strip() for part in value.split(",")]
    if len(parts) < 2:
        return None
    try:
        x = int(round(float(parts[0])))
        y = int(round(float(parts[1])))
    except ValueError:
        return None
    if x == 0 and y == 0:
        return None
    return MapPosition(x=x, y=y)


def _map_position_data(
    current: MowerMap, data: dict[str, Any], *, record_history: bool
) -> MowerMap:
    """Merge mower, station, and beacon positions into the map cache."""
    mower_position = _map_position(data.get("deebotPos"))
    if _unfixed_origin(mower_position, data):
        mower_position = None
    charge_positions = _map_positions(data.get("chargePos"))
    # G1 reports UWB beacon positions; O-series (RTK) reports rtkPos instead.
    uwb_positions = _map_positions(data.get("uwbPos")) or _map_positions(
        data.get("rtkPos")
    )
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
    uwb_positions = _map_positions(data.get("uwbPos")) or _map_positions(
        data.get("rtkPos")
    )
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
    if _map_chunks_ready(chunks, _int(serial)):
        path = _decode_trace_path(chunks) or trace.path
    else:
        path = trace.path

    return replace(
        current,
        # The active map id is owned solely by the live position stream
        # (:data:`_ACTIVE_MAP_ID_COMMANDS`). Trace replies only contribute
        # geometry for whichever map is already active; letting them change the
        # mid would make the next position push look like a remap and reset the
        # geometry we just decoded.
        mid=current.mid,
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
    # The map screen keeps a piece only when using is 1. Other copies are
    # inactive and must not enter the chunk set.
    using = _int(data.get("using"))
    if using is not None and using != 1:
        return current

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

    if _map_chunks_ready(chunks, _int(serial)):
        decoded_outline, decoded_obstacles = _decode_base_map(chunks)
    else:
        decoded_outline, decoded_obstacles = (), ()
    # An incomplete follow-up chunk must not erase a lawn that already decoded.
    # Replace the outline only when the new chunk set actually produces one.
    if decoded_outline:
        outline, obstacles = decoded_outline, decoded_obstacles
    else:
        outline, obstacles = current.info.outline, current.info.obstacles

    return replace(
        current,
        # See ``_map_trace_data``: base-map replies feed geometry but must never
        # re-own the active map id, which belongs to the live position stream.
        mid=current.mid,
        info=replace(
            map_info,
            batch_id=batch_id or map_info.batch_id,
            serial=serial or map_info.serial,
            info_size=_int(data.get("infoSize")) or map_info.info_size,
            type=map_type or map_info.type,
            chunks=chunks,
            outline=outline,
            obstacles=obstacles,
        ),
        last_update_ts=_int(data.get("_mqtt_ts")) or current.last_update_ts,
    )


def _reset_map_on_id_change(state: MowerState, data: Any) -> MowerState:
    """Drop stale map geometry when the active map id changes (remap).

    Resetting the mower and remapping produces a fresh ``mid``. The previously
    decoded base map outline, obstacles, live trace, mowed-area history, and
    charger/beacon positions all belong to the old map's coordinate frame, so
    keeping them would leave the integration showing the old base map while the
    mowed area drifts off it. Clearing the cached outline also lets the
    coordinator re-fetch ``getMapInfo_V2`` for the new map (it only requests the
    base map while no outline is cached). The live marker (``current_position``)
    is left untouched because it self-corrects from the next position push.

    This only runs for the authoritative live position/beacon stream
    (:data:`_ACTIVE_MAP_ID_COMMANDS`); base-map and trace replies never write
    the active map id (see ``_map_trace_data`` / ``_map_info_data``), so a
    geometry reply whose ``mid`` lives in a different namespace than the
    position stream cannot thrash the active map back and forth with the live
    stream or get spuriously discarded.
    """
    if not isinstance(data, dict):
        return state
    incoming = data.get("mid")
    if incoming is None:
        return state
    incoming = str(incoming)
    current_mid = state.map.mid
    if not current_mid or incoming == current_mid:
        return state
    return replace(
        state,
        map=replace(
            state.map,
            mid=incoming,
            info=MowerMapInfo(),
            trace=MowerMapTrace(),
            position_history=(),
            charge_positions=(),
            uwb_positions=(),
            revision=state.map.revision + 1,
        ),
    )


def _rtk_station(data: dict[str, Any]) -> MapPosition | None:
    """Return the RTK base station position from a getRTK/onRTK payload.

    The payload exposes ``rtks`` as a list, but an O-series setup has a single
    fixed base station, so the first valid entry is used.
    """
    stations = data.get("rtks")
    if not isinstance(stations, list):
        return None
    for item in stations:
        position = _map_position(item)
        if position is not None and position.invalid != 1:
            return position
    return None


def _map_mid_only(current: MowerMap, data: dict[str, Any]) -> MowerMap:
    """Record only the map id from an O-series map payload.

    The base-map and contour geometry for these replies arrives over MQTT (the
    HTTP reply only acknowledges), so we keep the existing geometry and just
    learn the current ``mid`` when present.
    """
    mid = data.get("mid")
    if mid is None or str(mid) == (current.mid or ""):
        return current
    return replace(current, mid=str(mid))


def _map_set_layer(current: MowerMap, data: dict[str, Any]) -> MowerMap:
    """Apply an O-series map-set layer (getMapTrack/getAreaSet).

    The payload is ``{mid, aid, type, subsets, infoSize}`` where ``subsets`` is a
    base64 + compact-LZMA blob (same wrapper as the G1 V2 map). ``type`` selects
    the layer: ``ar`` = mowing areas (anchor points), ``vw`` = virtual walls /
    no-go zones.
    """
    new = current
    mid = data.get("mid")
    if mid is not None and not current.mid:
        new = replace(new, mid=str(mid))

    decoded = _decode_map_subset(data.get("subsets"))
    if not isinstance(decoded, list):
        return new

    layer_type = data.get("type")
    if layer_type == "ar":
        new = replace(new, areas=_area_anchor_points(decoded))
    elif layer_type == "vw":
        new = replace(new, no_go_zones=_no_go_zone_polygons(decoded))
    return new


def _decode_map_subset(value: Any) -> Any:
    """Decode an O-series ``subsets`` blob (base64 + compact LZMA) to JSON."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return _decode_lzma_json_chunks({0: value})
    except (binascii.Error, ValueError, lzma.LZMAError, json.JSONDecodeError):
        return None


def _area_anchor_points(records: list[Any]) -> tuple[MapPosition, ...]:
    """Return the anchor point of each ``ar`` area record.

    Captured shape: ``["<id>","<type>","<name>","","<x>","<y>","<code>"]``.
    """
    points: list[MapPosition] = []
    for record in records:
        if not isinstance(record, list) or len(record) < 6:
            continue
        try:
            points.append(MapPosition(x=int(record[4]), y=int(record[5])))
        except (TypeError, ValueError):
            continue
    return tuple(points)


def _no_go_zone_polygons(
    records: list[Any],
) -> tuple[tuple[MapPosition, ...], ...]:
    """Return virtual-wall polygons from ``vw`` records (best-effort).

    No populated virtual-wall capture is available yet, so we parse defensively:
    a record contributes a polygon when it carries a semicolon-delimited
    coordinate string; otherwise it is skipped.
    """
    zones: list[tuple[MapPosition, ...]] = []
    for record in records:
        if not isinstance(record, list):
            continue
        for field in record:
            if isinstance(field, str) and ";" in field and "," in field:
                polygon = _positions_from_coordinate_string(field)
                if len(polygon) >= 2:
                    zones.append(polygon)
                break
    return tuple(zones)


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


def map_geometry_health(mower_map: MowerMap) -> dict[str, Any]:
    """Return whether map geometry decoded, without coordinates or identifiers.

    Diagnostics and logs can show this directly. It includes chunk indexes,
    sizes, and the decoder status only.
    """
    return {
        "info": _layer_health(
            mower_map.info.chunks,
            mower_map.info.info_size,
            mower_map.info.serial,
            outline_points=len(mower_map.info.outline),
            obstacle_count=len(mower_map.info.obstacles),
        ),
        "trace": _layer_health(
            mower_map.trace.chunks,
            mower_map.trace.info_size,
            mower_map.trace.serial,
            path_points=len(mower_map.trace.path),
        ),
        "position_history_points": len(mower_map.position_history),
        "has_current_position": mower_map.current_position is not None,
        "charge_position_count": len(mower_map.charge_positions),
    }


def _layer_health(
    chunks: dict[int, str],
    info_size: int | None,
    serial: str | None,
    **counts: int,
) -> dict[str, Any]:
    indexes = sorted(chunks)
    health: dict[str, Any] = {
        "chunk_indexes": indexes,
        "chunk_chars": [len(chunks[index]) for index in indexes],
        "info_size": info_size,
        "decode": _chunk_decode_status(chunks, _int(serial)),
        **counts,
    }
    reindexed = _reindexed_layer_facts(chunks)
    if reindexed is not None:
        health["reindexed"] = reindexed
    return health


def _reindexed_layer_facts(chunks: dict[int, str]) -> dict[str, Any] | None:
    """Try a 1-based chunk set as if it had been numbered from 0.

    This does not change the stored map. It only reports whether the bytes
    would decode, so a numbering mismatch can be told apart from a missing chunk.
    """
    indexes = sorted(chunks)
    if not indexes or indexes[0] == 0:
        return None
    if indexes != list(range(indexes[0], indexes[-1] + 1)):
        return None
    shifted = {index - indexes[0]: chunks[index] for index in indexes}
    status = _chunk_decode_status(shifted)
    facts: dict[str, Any] = {"decode": status}
    if status == "ok":
        outline, obstacles = _decode_base_map(shifted)
        facts["outline_points"] = len(outline)
        facts["obstacle_count"] = len(obstacles)
    return facts


def _map_chunks_ready(chunks: dict[int, str], serial: int | None) -> bool:
    """Return whether a chunk set is complete enough to decode.

    When ``serial`` is a positive piece count, indexes ``0`` through
    ``serial - 1`` must all be present. Otherwise a contiguous set that starts
    at 0 is enough, which covers a single unchunked blob.
    """
    if not chunks:
        return False
    indexes = sorted(chunks)
    if serial is not None and serial > 0:
        return indexes == list(range(serial))
    return indexes == list(range(indexes[-1] + 1))


def _chunk_decode_status(
    chunks: dict[int, str], serial: int | None = None
) -> str | None:
    """Return a secret-free decoder status for one chunk set."""
    if not chunks:
        return None
    if not _map_chunks_ready(chunks, serial):
        return "ValueError: Incomplete chunks"
    try:
        _decode_lzma_json_chunks(chunks)
    except json.JSONDecodeError:
        # A partial chunk set is not valid JSON yet. The message can quote
        # decoded text, so keep the type only.
        return "JSONDecodeError"
    except ValueError as err:
        return f"ValueError: {err}"
    except (binascii.Error, lzma.LZMAError) as err:
        return type(err).__name__
    return "ok"


def _decode_lzma_json_chunks(chunks: dict[int, str]) -> Any:
    """Decode ECOVACS' compact LZMA chunk wrapper into JSON.

    The app joins ``info`` strings in index order and base64-decodes that one
    string. A reply that base64-encodes each piece separately is accepted too.
    """
    if not chunks:
        raise ValueError("No chunks")
    indexes = sorted(chunks)
    if indexes != list(range(indexes[-1] + 1)):
        raise ValueError("Incomplete chunks")
    pieces = [chunks[index] for index in indexes]
    errors: list[BaseException] = []
    for raw in (
        _base64_bytes("".join(pieces)),
        _base64_bytes_per_piece(pieces),
    ):
        if raw is None:
            continue
        try:
            return _json_from_lzma_bytes(raw)
        except (ValueError, lzma.LZMAError, json.JSONDecodeError) as err:
            errors.append(err)
    if errors:
        raise errors[0]
    raise ValueError("Chunk payload too small")


def _base64_bytes(value: str) -> bytes | None:
    try:
        return base64.b64decode(value, validate=False)
    except binascii.Error:
        return None


def _base64_bytes_per_piece(pieces: list[str]) -> bytes | None:
    parts: list[bytes] = []
    for piece in pieces:
        decoded = _base64_bytes(piece)
        if decoded is None:
            return None
        parts.append(decoded)
    return b"".join(parts)


def _json_from_lzma_bytes(raw: bytes) -> Any:
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
