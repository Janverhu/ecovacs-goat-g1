"""Tests for ECOVACS mower message parsing."""

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

from custom_components.ecovacs_goat_g1.mower_messages import (
    apply_command_data,
    apply_mqtt_payload,
    apply_response,
)
from custom_components.ecovacs_goat_g1.mower_models import MowerActivity, MowerState
from custom_components.ecovacs_goat_g1.mower_api import (
    EcovacsApiError,
    _raise_for_control_error,
)


def test_grouped_get_info_updates_core_state() -> None:
    """Grouped app getInfo responses update core state in one pass."""
    state = apply_response(
        MowerState(),
        "getInfo",
        {
            "body": {
                "data": {
                    "getBattery": {"data": {"value": 95, "isLow": 0}},
                    "getCleanInfo_V2": {"data": {"trigger": "none", "state": "idle"}},
                    "getChargeState": {
                        "data": {"isCharging": 1, "mode": "slot"},
                    },
                    "getError": {"data": {"code": [0]}},
                }
            }
        },
    )

    assert state.battery == 95
    assert state.activity is MowerActivity.DOCKED
    assert state.charging is True
    assert state.charge_mode == "slot"
    assert state.error_code == 0


def test_grouped_get_info_keeps_docked_when_idle_follows_charging() -> None:
    """Captured getInfo ordering reports charge state before idle clean state."""
    state = apply_response(
        MowerState(),
        "getInfo",
        {
            "body": {
                "data": {
                    "getChargeState": {
                        "data": {"isCharging": 1, "mode": "slot"},
                    },
                    "getCleanInfo_V2": {"data": {"trigger": "none", "state": "idle"}},
                }
            }
        },
    )

    assert state.activity is MowerActivity.DOCKED
    assert state.charging is True


def test_grouped_get_info_reports_paused_when_clean_task_is_charging() -> None:
    """A charging pause during a clean task should not report as mowing."""
    state = apply_response(
        MowerState(),
        "getInfo",
        {
            "body": {
                "data": {
                    "getChargeState": {
                        "data": {"isCharging": 1, "mode": "slot"},
                    },
                    "getCleanInfo_V2": {
                        "data": {
                            "trigger": "none",
                            "state": "clean",
                            "cleanState": {"motionState": "pause"},
                        }
                    },
                }
            }
        },
    )

    assert state.activity is MowerActivity.PAUSED
    assert state.charging is True


def test_grouped_get_info_caches_task_id_for_app_style_writes() -> None:
    """Captured write payloads reuse the current task id from stats readbacks."""
    state = apply_response(
        MowerState(),
        "getInfo",
        {
            "body": {
                "data": {
                    "getStats": {
                        "data": {
                            "mowid": "12345",
                            "time": 1,
                            "area": 2538175,
                            "mowedArea": 1269088,
                        }
                    },
                    "getLastTimeStats": {"data": {"cid": "12345", "stop": 1}},
                }
            }
        },
    )

    assert state.task_id == "12345"
    assert state.stats.area == 1269088
    assert state.stats.job_area == 2538175
    assert state.stats.progress == 50.0


def test_stats_prefers_reported_progress_when_available() -> None:
    """The app may report progress separately from mowed-area ratio."""
    state = apply_command_data(
        MowerState(),
        "getStats",
        {
            "area": 2538175,
            "mowedArea": 1269088,
            "progress": 93,
        },
    )

    assert state.stats.area == 1269088
    assert state.stats.job_area == 2538175
    assert state.stats.progress == 93


def test_mqtt_setting_push_updates_cache() -> None:
    """Mower-specific MQTT pushes update settings without polling."""
    state = apply_mqtt_payload(
        MowerState(),
        "iot/atr/onAnimProtect/endpoint/77atlz/ONb7/j",
        b'{"body":{"data":{"enable":1,"start":"20:0","end":"8:0"}}}',
    )

    assert state.settings.animal_enabled is True
    assert state.settings.animal_start == "20:00"
    assert state.settings.animal_end == "08:00"


def test_mqtt_cut_efficiency_push_updates_cache() -> None:
    """Captured mowing efficiency pushes update settings without polling."""
    state = apply_mqtt_payload(
        MowerState(),
        "iot/atr/onCutEfficiency/endpoint/77atlz/ONb7/j",
        b'{"header":{"ver":"0.0.1"},"body":{"data":{"level":2},"code":0,"msg":"ok"}}',
    )

    assert state.settings.mowing_efficiency == "delicate"


def test_captured_mqtt_setting_burst_updates_cache() -> None:
    """Captured settings changed in the app update the cache from pushes."""
    state = MowerState()
    for topic, payload in (
        (
            "iot/atr/onCutDirection/endpoint/77atlz/ONb7/j",
            b'{"body":{"data":{"angle":90,"set":1}}}',
        ),
        (
            "iot/atr/onRainDelay/endpoint/77atlz/ONb7/j",
            b'{"body":{"data":{"enable":0,"delay":180}}}',
        ),
        (
            "iot/atr/onAnimProtect/endpoint/77atlz/ONb7/j",
            b'{"body":{"data":{"enable":0,"start":"21:00","end":"08:00"}}}',
        ),
        (
            "iot/atr/onBorderSwitch/endpoint/77atlz/ONb7/j",
            b'{"body":{"data":{"enable":0,"mode":0}}}',
        ),
        (
            "iot/atr/onObstacleHeight/endpoint/77atlz/ONb7/j",
            b'{"body":{"data":{"level":3}}}',
        ),
        (
            "iot/atr/onRecognization/endpoint/77atlz/ONb7/j",
            b'{"body":{"data":{"state":1,"update":0,"items":[]}}}',
        ),
        (
            "iot/atr/onChildLock/endpoint/77atlz/ONb7/j",
            b'{"body":{"data":{"on":1}}}',
        ),
    ):
        state = apply_mqtt_payload(state, topic, payload)

    assert state.settings.cut_direction == 90
    assert state.settings.rain_enabled is False
    assert state.settings.rain_delay == 180
    assert state.settings.animal_enabled is False
    assert state.settings.animal_start == "21:00"
    assert state.settings.animal_end == "08:00"
    assert state.settings.border_switch is False
    assert state.settings.border_mode == 0
    assert state.settings.obstacle_avoidance == "bumpy_tall_grass"
    assert state.settings.ai_recognition is True
    assert state.settings.safer_mode is True


def test_captured_lifecycle_and_battery_pushes_update_cache() -> None:
    """Captured mower command pushes update activity and battery without polling."""
    state = MowerState()
    for topic, payload in (
        (
            "iot/atr/onCleanInfo_V2/endpoint/77atlz/ONb7/j",
            b'{"body":{"data":{"trigger":"none","state":"clean","cleanState":{"motionState":"working"}}}}',
        ),
        (
            "iot/atr/onBattery/endpoint/77atlz/ONb7/j",
            b'{"body":{"data":{"value":94,"isLow":0}}}',
        ),
        (
            "iot/atr/onCleanInfo_V2/endpoint/77atlz/ONb7/j",
            b'{"body":{"data":{"trigger":"none","state":"clean","cleanState":{"motionState":"pause"}}}}',
        ),
        (
            "iot/atr/onCleanInfo_V2/endpoint/77atlz/ONb7/j",
            b'{"body":{"data":{"trigger":"none","state":"goCharging","cleanState":{"motionState":"goCharging"}}}}',
        ),
    ):
        state = apply_mqtt_payload(state, topic, payload)

    assert state.battery == 94
    assert state.activity is MowerActivity.RETURNING


def test_clean_pause_push_reports_paused_even_when_state_is_clean() -> None:
    """The app can report state=clean while motionState carries the pause."""
    state = apply_mqtt_payload(
        MowerState(activity=MowerActivity.MOWING),
        "iot/atr/onCleanInfo_V2/endpoint/77atlz/ONb7/j",
        b'{"body":{"data":{"trigger":"none","state":"clean","cleanState":{"motionState":"pause"}}}}',
    )

    assert state.activity is MowerActivity.PAUSED


def test_clean_working_clears_stale_charging_state() -> None:
    """A resumed working payload should win over stale charging state."""
    state = apply_mqtt_payload(
        MowerState(charging=True, activity=MowerActivity.DOCKED),
        "iot/atr/onCleanInfo_V2/endpoint/77atlz/ONb7/j",
        b'{"body":{"data":{"trigger":"none","state":"clean","cleanState":{"motionState":"working"}}}}',
    )

    assert state.activity is MowerActivity.MOWING
    assert state.charging is False


def test_protect_state_does_not_overwrite_rain_delay_setting() -> None:
    """Protection-state pushes are not the same as the rain-sensor setting."""
    state = apply_command_data(
        MowerState(),
        "getRainDelay",
        {"enable": 1, "delay": 180},
    )
    state = apply_command_data(
        state,
        "onProtectState",
        {"isAnimProtect": 0, "isRainProtect": 1, "isRainDelay": 0, "isLocked": 0},
    )

    assert state.settings.rain_enabled is True
    assert state.settings.rain_delay == 180


def test_stats_network_and_lifespan_parsing() -> None:
    """Direct app readbacks update diagnostics."""
    state = apply_command_data(
        MowerState(),
        "getWifiList",
        {
            "mac": "02:00:00:00:00:01",
            "list": [{"ssid": "Example WiFi", "rssi": 64, "ip": "192.0.2.10"}],
        },
    )
    state = apply_command_data(
        state,
        "getLifeSpan",
        [
            {"type": "blade", "left": 3367, "total": 4800},
            {"type": "lensBrush", "left": 1000, "total": 1000},
        ],
    )
    state = apply_command_data(
        state,
        "getTotalStats",
        {"area": 26067, "time": 647760, "count": 131},
    )

    assert state.network.ip == "192.0.2.10"
    assert state.network.rssi == 64
    assert state.lifespans["blade"] == 70.15
    assert state.lifespans["lensBrush"] == 100.0
    assert state.stats.total_count == 131


def test_get_robot_feature_populates_state() -> None:
    """getRobotFeature from grouped getInfo is merged into robot_features."""
    state = apply_response(
        MowerState(),
        "getInfo",
        {
            "body": {
                "data": {
                    "getRobotFeature": {
                        "data": {"4g": 1, "gps": 0, "station": 0},
                        "code": 0,
                        "msg": "ok",
                    },
                }
            }
        },
    )
    assert state.robot_features == {"4g": 1, "gps": 0, "station": 0}


def test_ngiot_body_code_failure_raises_api_error() -> None:
    """N-GIoT responses report command failures in body.code."""
    with pytest.raises(EcovacsApiError):
        _raise_for_control_error(
            "clean_V2",
            {"body": {"code": 500, "msg": "Request Timeout"}},
        )
