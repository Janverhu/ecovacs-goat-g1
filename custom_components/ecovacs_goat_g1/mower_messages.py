"""Parse ECOVACS GOAT mower responses and MQTT pushes."""

from __future__ import annotations

from dataclasses import replace
import json
from typing import Any

from .mower_models import (
    MowerActivity,
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
    4200: "Robot not reachable",
    500: "Request Timeout",
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
    data = body_data(decode_payload(payload))
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
                state = replace(
                    state,
                    activity=_clean_activity(data, state.activity),
                    task_id=_task_id(data, state.task_id),
                )
        case "onWorkState" | "getWorkState":
            if isinstance(data, dict):
                state = replace(state, activity=_work_state_activity(data, state.activity))
        case "getStats" | "onStats" | "reportStats":
            if isinstance(data, dict):
                state = replace(
                    state,
                    task_id=_task_id(data, state.task_id),
                    stats=replace(
                        state.stats,
                        area=_int(data.get("mowedArea", data.get("area"))),
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
                    activity=MowerActivity.ERROR if code not in (None, 0, 100) else state.activity,
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

    raw = dict(state.raw)
    raw[command] = data
    return replace(state, raw=raw, available=True)


def _clean_activity(data: dict[str, Any], current: MowerActivity) -> MowerActivity:
    state = data.get("state")
    clean_state = data.get("cleanState") or {}
    motion_state = clean_state.get("motionState")
    if data.get("trigger") == "alert":
        return MowerActivity.ERROR
    if state in ("clean", "washing") or motion_state == "working":
        return MowerActivity.MOWING
    if motion_state == "pause" or data.get("paused") == 1:
        return MowerActivity.PAUSED
    if state == "goCharging" or motion_state == "goCharging":
        return MowerActivity.RETURNING
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
    if station_state in ("emptying", "washing", "drying"):
        return MowerActivity.DOCKED
    if robot_state == "idle" and station_state == "idle":
        return MowerActivity.IDLE
    return current


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


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
