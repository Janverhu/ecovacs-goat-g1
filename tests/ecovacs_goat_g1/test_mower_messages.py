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
from custom_components.ecovacs_goat_g1.mower_models import (
    MapPosition,
    MowerActivity,
    MowerMap,
    MowerMapInfo,
    MowerMapTrace,
    MowerState,
)
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


def test_ngiot_null_json_body_is_not_an_error() -> None:
    """Some models return JSON null on successful control (no structured payload)."""
    _raise_for_control_error("clean_V2", None)
    _raise_for_control_error("appping", None)


def test_scheduled_clean_info_reports_mowing() -> None:
    """A scheduled job that fires on the mower should report as mowing (issue #7)."""
    state = apply_mqtt_payload(
        MowerState(activity=MowerActivity.IDLE),
        "iot/atr/onCleanInfo_V2/endpoint/77atlz/ONb7/j",
        b'{"body":{"data":{"trigger":"schedule","state":"working"}}}',
    )

    assert state.activity is MowerActivity.MOWING


def test_scheduled_trigger_does_not_override_returning() -> None:
    """A scheduled trigger reporting goCharging must not be forced to mowing."""
    state = apply_command_data(
        MowerState(),
        "getCleanInfo_V2",
        {"trigger": "schedule", "state": "goCharging"},
    )

    assert state.activity is MowerActivity.RETURNING


def test_o_series_clean_info_uses_shared_parser() -> None:
    """The O800 RTK getCleanInfo payload has the same fields as G1 getCleanInfo_V2."""
    state = apply_command_data(
        MowerState(),
        "getCleanInfo",
        {
            "trigger": "app",
            "other": "0",
            "state": "clean",
            "cleanState": {
                "motionState": "working",
                "cid": "122",
                "content": {"type": "auto", "subContent": {"type": "auto"}},
            },
        },
    )

    assert state.activity is MowerActivity.MOWING
    assert state.task_id == "122"


def test_o_series_rtk_position_drives_live_marker() -> None:
    """O-series getPos reports rtkPos instead of uwbPos; the marker still works."""
    state = apply_command_data(
        MowerState(activity=MowerActivity.MOWING),
        "getPos",
        {
            "deebotPos": {"x": 120, "y": -45, "a": 30, "invalid": 0},
            "chargePos": [{"x": 0, "y": 0, "a": 0, "t": 1, "invalid": 0}],
            "rtkPos": [{"x": 10, "y": 20, "invalid": 0}],
            "mid": "1",
        },
    )

    assert state.map.current_position is not None
    assert state.map.current_position.x == 120
    assert state.map.uwb_positions and state.map.uwb_positions[0].x == 10
    assert state.map.mid == "1"


def _state_with_decoded_map(mid: str) -> MowerState:
    """Build a state that already has decoded base map geometry for ``mid``."""
    outline = (MapPosition(x=0, y=0), MapPosition(x=10, y=0), MapPosition(x=10, y=10))
    return MowerState(
        map=MowerMap(
            mid=mid,
            current_position=MapPosition(x=5, y=5),
            charge_positions=(MapPosition(x=0, y=0),),
            uwb_positions=(MapPosition(x=1, y=1),),
            position_history=(MapPosition(x=2, y=2), MapPosition(x=3, y=3)),
            info=MowerMapInfo(batch_id="old", outline=outline),
            trace=MowerMapTrace(batch_id="old", path=(MapPosition(x=4, y=4),)),
            revision=7,
        )
    )


def test_remap_new_map_id_clears_stale_geometry() -> None:
    """A new map id (mower reset + remap) drops geometry from the old map frame."""
    state = _state_with_decoded_map("100")

    state = apply_command_data(
        state,
        "getPos",
        {
            "deebotPos": {"x": 50, "y": 60, "a": 90, "invalid": 0},
            "mid": "200",
        },
    )

    assert state.map.mid == "200"
    assert state.map.info.outline == ()
    assert state.map.info.batch_id is None
    assert state.map.trace.path == ()
    assert state.map.position_history == ()
    assert state.map.charge_positions == ()
    assert state.map.uwb_positions == ()
    assert state.map.revision > 7
    assert state.map.current_position is not None
    assert state.map.current_position.x == 50


def test_same_map_id_keeps_existing_geometry() -> None:
    """Repeated payloads for the same map id never discard decoded geometry."""
    state = _state_with_decoded_map("100")

    state = apply_command_data(
        state,
        "getPos",
        {
            "deebotPos": {"x": 50, "y": 60, "a": 90, "invalid": 0},
            "mid": "100",
        },
    )

    assert state.map.mid == "100"
    assert state.map.info.outline != ()
    assert state.map.trace.path != ()


def test_base_map_reply_does_not_re_own_active_map_id() -> None:
    """Base-map replies feed geometry but never re-own the active map id.

    The G1 position stream and the ``getMapInfo_V2`` geometry reply can report
    ``mid`` values from different namespaces, so a base-map reply must be
    applied for the active map (not discarded) while leaving the active map id
    owned by the live position stream. Discarding it was the regression that
    made the mowed-area outline disappear.
    """
    state = _state_with_decoded_map("100")

    state = apply_command_data(
        state,
        "onMapInfo_V2",
        {"mid": "200", "batid": "fresh", "serial": "0", "type": "0", "index": 0,
         "info": "ignored"},
    )

    assert state.map.mid == "100"
    assert state.map.info.batch_id == "fresh"


def test_trace_reply_does_not_re_own_active_map_id() -> None:
    """Trace replies advance the trace but never re-own the active map id.

    Letting a trace reply rewrite the active map id made the next position push
    look like a remap, which reset the trace and left the live segment growing
    into a continuous trace. The trace reply is applied while the position
    stream keeps ownership of the active map id.
    """
    state = _state_with_decoded_map("100")

    state = apply_command_data(
        state,
        "onMapTrace_V2",
        {"mid": "200", "batid": "fresh", "serial": "0", "type": "0", "index": 0,
         "info": "ignored"},
    )

    assert state.map.mid == "100"
    assert state.map.trace.batch_id == "fresh"


def test_trace_reply_for_active_map_is_applied() -> None:
    """A trace reply for the active map id is accepted and updates trace metadata."""
    state = _state_with_decoded_map("100")

    state = apply_command_data(
        state,
        "onMapTrace_V2",
        {"mid": "100", "batid": "fresh", "serial": "0", "type": "0", "index": 0,
         "info": "ignored"},
    )

    assert state.map.mid == "100"
    assert state.map.trace.batch_id == "fresh"


def test_base_map_reply_never_switches_active_map() -> None:
    """Only the position stream switches maps; geometry replies cannot flip it."""
    state = _state_with_decoded_map("100")

    state = apply_command_data(
        state,
        "onMapInfo_V2",
        {"mid": "200", "batid": "other", "serial": "0", "type": "0", "index": 0,
         "info": "ignored"},
    )

    assert state.map.mid == "100"


def test_o_series_rtk_station_position_parsed() -> None:
    """getRTK exposes the single fixed base station shown in place of beacons."""
    state = apply_command_data(
        MowerState(),
        "getRTK",
        {
            "result": 0,
            "rtks": [
                {"x": 1234, "y": 5678, "sn": "RTKSN0001", "state": 0, "mode": 0}
            ],
            "observations": {"solStat": 0, "roverSvs": 33},
        },
    )

    assert state.map.rtk_station is not None
    assert state.map.rtk_station.x == 1234
    assert state.map.rtk_station.y == 5678
    assert state.map.as_dict()["rtk_station"] == {
        "x": 1234,
        "y": 5678,
        "sn": "RTKSN0001",
    }


def test_o_series_rtk_empty_list_keeps_no_station() -> None:
    """An empty rtks list must not invent a station marker."""
    state = apply_command_data(MowerState(), "getRTK", {"result": 0, "rtks": []})
    assert state.map.rtk_station is None


def _make_subset(obj) -> str:
    """Build an O-series ``subsets`` blob (base64 + compact LZMA wrapper)."""
    import base64
    import json as _json
    import lzma

    raw = _json.dumps(obj, separators=(",", ":")).encode()
    comp = lzma.compress(
        raw,
        format=lzma.FORMAT_RAW,
        filters=[{"id": lzma.FILTER_LZMA1, "dict_size": 0x40000, "lc": 3, "lp": 0, "pb": 2}],
    )
    header = bytes([0x5D]) + (0x40000).to_bytes(4, "little") + len(raw).to_bytes(4, "little")
    return base64.b64encode(header + comp).decode()


def test_o_series_area_set_decodes_to_anchor_points() -> None:
    """getAreaSet 'ar' subsets decode (shared LZMA) to area anchor points."""
    subset = _make_subset([["1", "1", "Lawn", "", "100", "200", "0-0"]])
    state = apply_command_data(
        MowerState(),
        "getAreaSet",
        {"mid": "1", "aid": "0", "type": "ar", "subsets": subset, "infoSize": 1},
    )

    assert [p.as_dict() for p in state.map.areas] == [{"x": 100, "y": 200}]
    assert state.map.mid == "1"


def test_o_series_empty_virtual_walls_decode_to_no_zones() -> None:
    """The real captured empty 'vw' subset decodes to no no-go zones."""
    state = apply_command_data(
        MowerState(),
        "getMapTrack",
        {
            "mid": "1",
            "aid": "0",
            "type": "vw",
            "subsets": "XQAABAACAAAAAC2XPAAAAA==",
            "infoSize": 2,
        },
    )

    assert state.map.no_go_zones == ()
    assert state.map.mid == "1"


def test_o_series_virtual_wall_polygon_best_effort_decode() -> None:
    """A 'vw' record carrying a coordinate string yields a no-go polygon."""
    subset = _make_subset([["1", "1", "", "", "3;10,20;30,40;50,60"]])
    state = apply_command_data(
        MowerState(), "getMapTrack", {"mid": "1", "type": "vw", "subsets": subset}
    )

    assert len(state.map.no_go_zones) == 1
    assert [p.as_dict() for p in state.map.no_go_zones[0]] == [
        {"x": 10, "y": 20},
        {"x": 30, "y": 40},
        {"x": 50, "y": 60},
    ]


def test_o_series_map_state_learns_mid_without_decoding() -> None:
    """O-series map payloads only contribute the map id, never bogus geometry."""
    state = apply_command_data(
        MowerState(),
        "getMapTrack",
        {"mid": "987654", "totalCount": 400, "value": "<binary-blob>"},
    )

    assert state.map.mid == "987654"
    assert state.map.info.outline == ()
    assert state.map.trace.path == ()
