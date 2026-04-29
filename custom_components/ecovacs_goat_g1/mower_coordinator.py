"""Coordinator for ECOVACS GOAT mower entities."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import replace
import logging
from math import atan2, degrees, hypot
from time import monotonic, time
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .debug_capture import DebugCaptureStore
from .mower_api import EcovacsApiError, EcovacsMowerApi
from .mower_messages import (
    MOWING_EFFICIENCY_LEVELS,
    OBSTACLE_AVOIDANCE_LEVELS,
    apply_command_data,
    apply_mqtt_payload,
    apply_response,
)
from .mower_models import MapPosition, MowerActivity, MowerDevice, MowerState
from .mower_mqtt import MowerAppPresenceMqttClient, MowerMqttClient

_LOGGER = logging.getLogger(__name__)
FRESH_STATE_SECONDS = 300
MAP_TRACE_TYPE = "0"
APP_LIVE_MAP_TYPES = ("ar", "vw", "fe")
MQTT_READBACK_DEBOUNCE_SECONDS = 3
MAP_TRACE_DIRECTION_THRESHOLD_DEGREES = 90
MAP_TRACE_POSITION_HEADING_MIN_DISTANCE = 20
LIVE_POSITION_SEGMENT_MAX_POINTS = 800
LIVE_POSITION_STREAM_REQUEST_MIN_INTERVAL_SECONDS = 60
APP_PING_INTERVAL_SECONDS = 120
APP_PRESENCE_MQTT_TTL_SECONDS = APP_PING_INTERVAL_SECONDS + 30
COMMAND_VERIFY_INITIAL_DELAY_SECONDS = 3
COMMAND_VERIFY_INTERVAL_SECONDS = 6
COMMAND_VERIFY_TIMEOUT_SECONDS = 90
RETURNING_REFRESH_SECONDS = 10
MOWING_POSITION_REFRESH_SECONDS = 60
POSITION_MQTT_STALE_SECONDS = 60
MAP_HISTORY_STORE_VERSION = 1
MAP_HISTORY_STORE_DELAY_SECONDS = 5

# Polling policy:
# - Prefer MQTT pushes for normal state and live movement. In particular, onPos
#   should drive the live marker whenever ECOVACS publishes it.
# - Poll while mowing only as a gap-filler after onPos has been quiet for a
#   while; this avoids using frequent cloud reads as the animation source.
# - Poll while returning only until a terminal state is observed, because ECOVACS
#   may stop position pushes near the dock and HA still needs to notice docking.
# - Do not live-position poll for stable paused, stopped, idle, or docked states.
# - Gate mower-provided map trace pushes by accumulated heading change. Trace
#   payloads are heavier than onPos; accepting them after a turn keeps completed
#   mowing lines fresh without redrawing the full area on every trace push.
# - The fast app-style live-position stream is requested only through the
#   request_live_position_stream service. The custom card can start an explicit
#   keepalive window; background mowing refreshes stay at a slow getPos cadence.
ACTIONABLE_MQTT_READBACK_COMMANDS = {
    "onAnimProtect",
    "onBorderSwitch",
    "onBreakPointStatus",
    "onChargeState",
    "onChildLock",
    "onCleanInfo",
    "onCleanInfo_V2",
    "onCrossMapBorderWarning",
    "onCutDirection",
    "onCutEfficiency",
    "onError",
    "onMoveupWarning",
    "onObstacleHeight",
    "onProtectState",
    "onRainDelay",
    "onRecognization",
    "onStats",
    "onWorkState",
    "reportStats",
}
CUT_DIRECTION_LOCKED_ACTIVITIES = {
    MowerActivity.MOWING,
    MowerActivity.PAUSED,
    MowerActivity.RETURNING,
}
LIVE_POSITION_STREAM_ACTIVITIES = {
    MowerActivity.MOWING,
    MowerActivity.RETURNING,
}
COMMANDS_WITH_TASK_ID = {
    "charge",
    "clean_V2",
    "setAnimProtect",
    "setBorderSwitch",
    "setChildLock",
    "setCrossMapBorderWarning",
    "setCutDirection",
    "setCutEfficiency",
    "setMoveupWarning",
    "setObstacleHeight",
    "setRainDelay",
    "setRecognization",
}

STARTUP_GET_INFO_GROUPS = (
    (
        "getUWB",
        "getMapState",
        "getChargeState",
        "getCleanInfo_V2",
        "getOta",
        "getRobotFeature",
    ),
    (
        "getBattery",
        "getBreakPointStatus",
        "getStats",
        "getError",
        "getLastTimeStats",
        "getMapUpdate",
        "getRelocationState",
    ),
    (
        "getProtectState",
        "getRecognization",
        "getNetworkSwitch",
        "getScheduleLatestTask",
        "getApnList",
        "getObstacleHeight",
        "getHumanoidWarning",
    ),
    (
        "getAnimProtect",
        "getCutEfficiency",
        "getBoundOpt",
        "getRainDelay",
        "getCutDirection",
        "getMoveupWarning",
        "getCrossMapBorderWarning",
        "getSleep",
        "getSafeProtect",
        "getRemoteSupport",
        "getGeolocation",
        "getChildLock",
        "getBorderSwitch",
    ),
)


def _mqtt_command(topic: str) -> str:
    """Return the command segment from an ECOVACS MQTT topic."""
    return topic.split("/")[2] if "/" in topic else topic


class MowerCoordinator(DataUpdateCoordinator[MowerState]):
    """Data coordinator for one mower."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: EcovacsMowerApi,
        device: MowerDevice,
        debug_capture: DebugCaptureStore | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"ECOVACS GOAT {device.did}",
        )
        self.api = api
        self.device = device
        self._debug_capture = debug_capture
        self.data = MowerState()
        self._last_mqtt_at: float | None = None
        self._last_position_mqtt_at: float | None = None
        self._last_position_heading: float | None = None
        self._last_position_path_heading: float | None = None
        self._trace_heading_delta: float = 0
        self._trace_update_due = True
        self._last_readback_at: float | None = None
        self._mqtt_readback_task: asyncio.Task[None] | None = None
        self._outcome_refresh_tasks: dict[str, asyncio.Task[None]] = {}
        self._returning_refresh_task: asyncio.Task[None] | None = None
        self._mowing_position_refresh_task: asyncio.Task[None] | None = None
        self._trace_refresh_task: asyncio.Task[None] | None = None
        self._live_position_stream_task: asyncio.Task[None] | None = None
        self._live_position_keepalive_task: asyncio.Task[None] | None = None
        self._live_position_keepalive_until: float | None = None
        self._app_presence_stop_task: asyncio.Task[None] | None = None
        self._app_presence_stop_at: float | None = None
        self._startup_live_map_task: asyncio.Task[None] | None = None
        self._last_live_position_stream_request_at: float | None = None
        self._live_map_request_counter = 0
        self._stop_unsub: Callable[[], None] | None = None
        self._stopped = False
        store_key = f"ecovacs_goat_g1_map_history_{device.did}".replace("/", "_")
        self._map_history_store: Store[dict[str, Any]] = Store(
            hass, MAP_HISTORY_STORE_VERSION, store_key
        )
        self._saved_position_history: tuple[MapPosition, ...] = ()
        self._mqtt = MowerMqttClient(
            api,
            device,
            hass.loop,
            self._handle_mqtt_message,
            debug_capture,
        )
        self._app_presence_mqtt = MowerAppPresenceMqttClient(
            api,
            device,
            hass.loop,
            debug_capture,
        )

    async def async_start(self) -> None:
        """Start push subscription after initial state refresh."""
        position_history = await self._async_load_position_history()
        if position_history:
            self.data = replace(
                self.data,
                map=replace(self.data.map, position_history=position_history),
            )
            self._saved_position_history = position_history
        await self.async_config_entry_first_refresh()
        await self._mqtt.start()
        self._stop_unsub = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._async_handle_hass_stop
        )
        if self.data and self.data.activity is MowerActivity.MOWING:
            self._ensure_mowing_position_refresh()
        self._startup_live_map_task = self._create_background_task(
            self._async_refresh_live_map_after_mqtt_start(),
            "ecovacs_goat_startup_live_map",
        )

    async def async_stop(self) -> None:
        """Stop push subscription."""
        if self._stopped:
            return
        self._stopped = True
        if self._stop_unsub is not None and not self.hass.is_stopping:
            self._stop_unsub()
        self._stop_unsub = None
        if self._mqtt_readback_task and not self._mqtt_readback_task.done():
            self._mqtt_readback_task.cancel()
        if self._returning_refresh_task and not self._returning_refresh_task.done():
            self._returning_refresh_task.cancel()
        if (
            self._mowing_position_refresh_task
            and not self._mowing_position_refresh_task.done()
        ):
            self._mowing_position_refresh_task.cancel()
        if self._trace_refresh_task and not self._trace_refresh_task.done():
            self._trace_refresh_task.cancel()
        if (
            self._live_position_stream_task
            and not self._live_position_stream_task.done()
        ):
            self._live_position_stream_task.cancel()
        if (
            self._live_position_keepalive_task
            and not self._live_position_keepalive_task.done()
        ):
            self._live_position_keepalive_task.cancel()
        if self._app_presence_stop_task and not self._app_presence_stop_task.done():
            self._app_presence_stop_task.cancel()
        if self._startup_live_map_task and not self._startup_live_map_task.done():
            self._startup_live_map_task.cancel()
        for task in self._outcome_refresh_tasks.values():
            task.cancel()
        await asyncio.gather(
            *(
                task
                for task in (
                    self._mqtt_readback_task,
                    self._returning_refresh_task,
                    self._mowing_position_refresh_task,
                    self._trace_refresh_task,
                    self._live_position_stream_task,
                    self._live_position_keepalive_task,
                    self._app_presence_stop_task,
                    self._startup_live_map_task,
                    *self._outcome_refresh_tasks.values(),
                )
                if task
            ),
            return_exceptions=True,
        )
        self._mqtt_readback_task = None
        self._returning_refresh_task = None
        self._mowing_position_refresh_task = None
        self._trace_refresh_task = None
        self._live_position_stream_task = None
        self._live_position_keepalive_task = None
        self._live_position_keepalive_until = None
        self._app_presence_stop_task = None
        self._app_presence_stop_at = None
        self._startup_live_map_task = None
        self._outcome_refresh_tasks.clear()
        await self._app_presence_mqtt.stop()
        await self._map_history_store.async_save(self._position_history_payload())
        await self._mqtt.stop()

    async def _async_handle_hass_stop(self, _event: Event) -> None:
        """Cancel background tasks early in Home Assistant shutdown."""
        self._stop_unsub = None
        await self.async_stop()

    def async_set_updated_data(self, data: MowerState) -> None:
        """Set coordinator data and persist the last mowing path."""
        super().async_set_updated_data(data)
        self._schedule_position_history_save(data.map.position_history)

    @property
    def debug_capture(self) -> DebugCaptureStore:
        """Return the shared debug capture store."""
        assert self._debug_capture is not None
        return self._debug_capture

    async def _async_load_position_history(self) -> tuple[MapPosition, ...]:
        """Restore the last mowing path from HA storage."""
        stored = await self._map_history_store.async_load()
        if not isinstance(stored, dict):
            return ()
        return tuple(
            position
            for item in stored.get("position_history", [])
            if isinstance(item, dict)
            for position in (MapPosition.from_payload(item),)
            if position is not None
        )

    def _schedule_position_history_save(
        self, position_history: tuple[MapPosition, ...]
    ) -> None:
        """Debounce writes of the last mowing path to HA storage."""
        if position_history == self._saved_position_history:
            return
        self._saved_position_history = position_history
        self._map_history_store.async_delay_save(
            self._position_history_payload,
            MAP_HISTORY_STORE_DELAY_SECONDS,
        )

    def _position_history_payload(self) -> dict[str, Any]:
        """Return the persisted map history payload."""
        position_history = self.data.map.position_history if self.data else ()
        return {
            "position_history": [
                position.as_dict() for position in position_history
            ],
        }

    async def _async_update_data(self) -> MowerState:
        """Refresh from the mower using a small app-style command set."""
        try:
            state = await self._async_refresh_state_groups()
            for command, payload in (
                ("getWifiList", {}),
                ("getLifeSpan", {}),
                ("getTotalStats", {}),
            ):
                response = await self.api.control(self.device, command, payload)
                state = apply_response(state, command, response)
        except EcovacsApiError as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected ECOVACS update error: {err}") from err

        return state

    def _handle_mqtt_message(self, topic: str, payload: bytes) -> None:
        """Handle a pushed MQTT message from paho's thread."""
        def update_state() -> None:
            try:
                command = _mqtt_command(topic)
                now = monotonic()
                if command == "onMapTrace_V2" and not self._should_accept_trace_mqtt():
                    self._last_mqtt_at = now
                    self._capture_event(
                        "mqtt_trace_throttled",
                        {
                            "command": command,
                            "accumulated_heading_delta": round(
                                self._trace_heading_delta, 1
                            ),
                            "threshold_degrees": MAP_TRACE_DIRECTION_THRESHOLD_DEGREES,
                        },
                    )
                    return
                self._last_mqtt_at = now
                previous_state = self.data
                state = apply_mqtt_payload(self.data, topic, payload)
                if command == "onPos":
                    self._last_position_mqtt_at = self._last_mqtt_at
                    self._update_trace_direction_gate(state)
                    state = self._compact_live_position_segment(state)
                if command == "onMapTrace_V2":
                    if self._trace_path_changed(previous_state, state):
                        state = self._reset_live_position_segment(state)
                        self._mark_trace_mqtt_applied()
                self.async_set_updated_data(state)
                self._capture_event(
                    "mqtt_parsed",
                    {
                        "command": command,
                        "activity": state.activity.value
                        if state.activity is not None
                        else None,
                        "map_revision": state.map.revision,
                        "handled": True,
                    },
                )
                if command in ACTIONABLE_MQTT_READBACK_COMMANDS:
                    self._schedule_mqtt_readback()
                if state.activity is MowerActivity.MOWING:
                    self._ensure_mowing_position_refresh()
                if state.activity is MowerActivity.RETURNING:
                    self._ensure_returning_refresh()
            except Exception as err:
                _LOGGER.exception("Failed to parse ECOVACS MQTT message %s", topic)
                self._capture_event(
                    "mqtt_parse_error",
                    {
                        "topic": topic,
                        "command": _mqtt_command(topic),
                        "payload_size": len(payload),
                        "exception": repr(err),
                    },
                )

        self.hass.loop.call_soon_threadsafe(update_state)

    def _should_accept_trace_mqtt(self) -> bool:
        """Return whether an incoming mower trace push should update HA state."""
        if not self.data or not self.data.map.trace.path:
            return True
        return self._trace_update_due

    def _update_trace_direction_gate(self, state: MowerState) -> None:
        """Mark trace refresh due once stepped heading changes add up enough."""
        current = state.map.current_position
        if current is None or current.invalid == 1:
            return

        deltas: list[float] = []
        heading = current.a
        if heading is not None:
            current_heading = float(heading)
            if self._last_position_heading is not None:
                deltas.append(
                    abs(self._angle_delta(self._last_position_heading, current_heading))
                )
            self._last_position_heading = current_heading

        path_heading = self._path_heading_from_previous_position(current)
        if path_heading is not None:
            if self._last_position_path_heading is not None:
                deltas.append(
                    abs(
                        self._angle_delta(
                            self._last_position_path_heading, path_heading
                        )
                    )
                )
            self._last_position_path_heading = path_heading

        if not deltas:
            return

        self._trace_heading_delta += max(deltas)
        if self._trace_heading_delta >= MAP_TRACE_DIRECTION_THRESHOLD_DEGREES:
            self._trace_update_due = True
            self._schedule_trace_refresh()

    def _path_heading_from_previous_position(
        self, current: MapPosition
    ) -> float | None:
        """Return movement-derived heading for trace gating."""
        previous = self.data.map.current_position if self.data else None
        if previous is None or previous.invalid == 1:
            return None
        dx = current.x - previous.x
        dy = current.y - previous.y
        if hypot(dx, dy) < MAP_TRACE_POSITION_HEADING_MIN_DISTANCE:
            return None
        return degrees(atan2(dy, dx))

    def _mark_trace_mqtt_applied(self) -> None:
        """Reset the trace gate after applying a mower trace push."""
        self._trace_heading_delta = 0
        self._trace_update_due = False
        self._last_position_heading = None
        self._last_position_path_heading = None

    @staticmethod
    def _trace_path_changed(previous: MowerState | None, current: MowerState) -> bool:
        """Return whether the mower-provided trace path actually advanced."""
        if not current.map.trace.path:
            return False
        return previous is None or previous.map.trace.path != current.map.trace.path

    def _compact_live_position_segment(self, state: MowerState) -> MowerState:
        """Keep the live position segment since the last trace commit."""
        current = state.map.current_position
        if (
            state.activity is not MowerActivity.MOWING
            or current is None
            or current.invalid == 1
        ):
            return state

        history = state.map.position_history
        compact_history = history or (current,)
        if compact_history[-1] != current:
            compact_history = (*compact_history, current)
        if len(compact_history) > LIVE_POSITION_SEGMENT_MAX_POINTS:
            compact_history = compact_history[-LIVE_POSITION_SEGMENT_MAX_POINTS:]
        if compact_history == history:
            return state
        return replace(
            state,
            map=replace(state.map, position_history=compact_history),
        )

    def _reset_live_position_segment(self, state: MowerState) -> MowerState:
        """Start a new live segment after the mower trace has caught up."""
        current = state.map.current_position
        history = (current,) if current is not None and current.invalid != 1 else ()
        if state.map.position_history == history:
            return state
        return replace(
            state,
            map=replace(state.map, position_history=history),
        )

    def _schedule_trace_refresh(self) -> None:
        """Request the mower trace after enough live heading change."""
        if self._trace_refresh_task and not self._trace_refresh_task.done():
            return
        self._trace_refresh_task = self._create_background_task(
            self._async_refresh_trace_after_turn(),
            "ecovacs_goat_trace_after_turn",
        )

    async def _async_refresh_trace_after_turn(self) -> None:
        """Refresh mower-provided trace after the direction gate opens."""
        await asyncio.sleep(0.5)
        if not self.data or not self.data.map.mid or not self._trace_update_due:
            return
        try:
            response = await self.api.control(
                self.device,
                "getMapTrace_V2",
                {"mid": self.data.map.mid, "type": MAP_TRACE_TYPE},
            )
            previous_state = self.data
            state = apply_response(previous_state, "getMapTrace_V2", response)
            trace_changed = self._trace_path_changed(previous_state, state)
            if trace_changed:
                state = self._reset_live_position_segment(state)
                self._mark_trace_mqtt_applied()
            self.async_set_updated_data(state)
            self._capture_event(
                "trace_refresh_after_turn",
                {
                    "trace_changed": trace_changed,
                    "threshold_degrees": MAP_TRACE_DIRECTION_THRESHOLD_DEGREES,
                    "map_revision": state.map.revision,
                },
            )
        except EcovacsApiError as err:
            _LOGGER.debug("ECOVACS trace refresh after turn failed: %s", err)
            self._capture_event(
                "trace_refresh_after_turn_error",
                {"exception": repr(err)},
            )
        finally:
            if self._trace_refresh_task is asyncio.current_task():
                self._trace_refresh_task = None

    @staticmethod
    def _angle_delta(previous: float, current: float) -> float:
        """Return the shortest signed angular delta between two headings."""
        return (current - previous + 180) % 360 - 180

    def _schedule_mqtt_readback(self) -> None:
        """Debounce a full readback after MQTT reports an actionable change."""
        if self._mqtt_readback_task and not self._mqtt_readback_task.done():
            self._mqtt_readback_task.cancel()
        self._mqtt_readback_task = self._create_background_task(
            self._async_debounced_mqtt_readback(),
            "ecovacs_goat_mqtt_readback",
        )

    async def _async_debounced_mqtt_readback(self) -> None:
        """Refresh grouped data after related MQTT pushes have settled."""
        try:
            await asyncio.sleep(MQTT_READBACK_DEBOUNCE_SECONDS)
            self.async_set_updated_data(await self._async_refresh_state_groups())
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("ECOVACS actionable MQTT readback failed", exc_info=True)
            self._capture_event(
                "mqtt_readback_error",
                {"exception": repr(err)},
            )

    def _ensure_returning_refresh(self) -> None:
        """Poll lightly while returning because ECOVACS may stop position pushes at dock."""
        if self._returning_refresh_task and not self._returning_refresh_task.done():
            return
        self._returning_refresh_task = self._create_background_task(
            self._async_refresh_while_returning(),
            "ecovacs_goat_returning_refresh",
        )

    async def _async_refresh_while_returning(self) -> None:
        """Refresh until the mower reports a final docked/idle state."""
        while self.data and self.data.activity is MowerActivity.RETURNING:
            await asyncio.sleep(RETURNING_REFRESH_SECONDS)
            if not self.data or self.data.activity is not MowerActivity.RETURNING:
                return
            self.async_set_updated_data(await self._async_refresh_state_groups())

    def _ensure_mowing_position_refresh(self) -> None:
        """Start the conservative live-position fallback while mowing.

        MQTT onPos is the preferred position source. This task only fills gaps when
        onPos has gone stale; it is not intended to drive normal map animation.
        """
        if (
            self._mowing_position_refresh_task
            and not self._mowing_position_refresh_task.done()
        ):
            return
        self._mowing_position_refresh_task = self._create_background_task(
            self._async_refresh_position_while_mowing(),
            "ecovacs_goat_mowing_position_refresh",
        )

    async def _async_refresh_position_while_mowing(self) -> None:
        """Refresh live position only when mowing and MQTT position is stale."""
        while self.data and self.data.activity is MowerActivity.MOWING:
            await asyncio.sleep(MOWING_POSITION_REFRESH_SECONDS)
            if not self.data or self.data.activity is not MowerActivity.MOWING:
                return
            if self._has_recent_position_mqtt():
                continue
            try:
                state = await self._async_refresh_live_position(self.data)
                self.async_set_updated_data(self._compact_live_position_segment(state))
            except EcovacsApiError as err:
                _LOGGER.debug("ECOVACS mowing position refresh failed: %s", err)
                self._capture_event(
                    "mowing_position_refresh_error",
                    {"exception": repr(err)},
                )

    def _has_recent_position_mqtt(self) -> bool:
        """Return whether MQTT has recently provided live mower position."""
        return (
            self._last_position_mqtt_at is not None
            and monotonic() - self._last_position_mqtt_at <= POSITION_MQTT_STALE_SECONDS
        )

    async def async_request_live_position_stream(
        self,
        reason: str,
        *,
        force: bool = False,
        duration_seconds: int | None = None,
    ) -> None:
        """Request fast app-style position updates for a visible live map card."""
        if duration_seconds is not None and duration_seconds > 0:
            self._extend_live_position_keepalive(
                reason,
                duration_seconds=duration_seconds,
                force=force,
            )
            return

        state = await self._async_request_live_position_stream(
            self.data or MowerState(),
            reason,
            force=force,
        )
        self.async_set_updated_data(self._compact_live_position_segment(state))

    def _extend_live_position_keepalive(
        self,
        reason: str,
        *,
        duration_seconds: int,
        force: bool,
    ) -> None:
        """Keep the app-style map session alive for an explicit short window."""
        duration_seconds = max(1, duration_seconds)
        until = monotonic() + duration_seconds
        self._live_position_keepalive_until = max(
            self._live_position_keepalive_until or 0,
            until,
        )
        self._capture_event(
            "live_position_keepalive_extended",
            {
                "reason": reason,
                "force": force,
                "duration_seconds": duration_seconds,
                "until_in_seconds": round(
                    self._live_position_keepalive_until - monotonic(), 1
                ),
            },
        )
        if (
            self._live_position_keepalive_task is None
            or self._live_position_keepalive_task.done()
        ):
            self._live_position_keepalive_task = self._create_background_task(
                self._async_live_position_keepalive(reason, force=force),
                "ecovacs_goat_live_position_keepalive",
            )

    async def _async_live_position_keepalive(
        self, reason: str, *, force: bool
    ) -> None:
        """Send app-style ping/map requests during an explicit keepalive window."""
        try:
            while (
                self._live_position_keepalive_until is not None
                and monotonic() < self._live_position_keepalive_until
            ):
                state = self.data or MowerState()
                if force or state.activity in LIVE_POSITION_STREAM_ACTIVITIES:
                    try:
                        await self._async_send_app_ping(reason)
                        state = await self._async_request_live_position_stream(
                            state,
                            f"{reason}_keepalive",
                            force=force,
                        )
                        self.async_set_updated_data(
                            self._compact_live_position_segment(state)
                        )
                    except EcovacsApiError as err:
                        _LOGGER.debug("ECOVACS live position keepalive failed: %s", err)
                        self._capture_event(
                            "live_position_keepalive_error",
                            {"reason": reason, "exception": repr(err)},
                        )

                if self._live_position_keepalive_until is None:
                    return
                delay = min(
                    APP_PING_INTERVAL_SECONDS,
                    max(0, self._live_position_keepalive_until - monotonic()),
                )
                if delay <= 0:
                    return
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        finally:
            if self._live_position_keepalive_task is asyncio.current_task():
                self._live_position_keepalive_task = None
                self._live_position_keepalive_until = None

    async def _async_send_app_ping(self, reason: str) -> None:
        """Send the GOAT app's lightweight MQTT keepalive command."""
        await self.api.control(self.device, "appping", {})
        self._capture_event("app_ping_sent", {"reason": reason})

    def _schedule_live_position_stream_request(
        self, reason: str, *, force: bool = False
    ) -> None:
        """Request the app-style live map stream in the background."""
        if self._live_position_stream_task and not self._live_position_stream_task.done():
            return
        self._live_position_stream_task = self._create_background_task(
            self._async_request_live_position_stream_background(reason, force=force),
            "ecovacs_goat_live_position_stream",
        )

    async def _async_request_live_position_stream_background(
        self, reason: str, *, force: bool
    ) -> None:
        """Run the app-style live map stream request and merge any readbacks."""
        try:
            state = await self._async_request_live_position_stream(
                self.data or MowerState(),
                reason,
                force=force,
            )
            self.async_set_updated_data(self._compact_live_position_segment(state))
        except asyncio.CancelledError:
            raise
        except EcovacsApiError as err:
            _LOGGER.debug("ECOVACS live position stream request failed: %s", err)
            self._capture_event(
                "live_position_stream_request_error",
                {"reason": reason, "exception": repr(err)},
            )
        finally:
            if self._live_position_stream_task is asyncio.current_task():
                self._live_position_stream_task = None

    async def _async_request_live_position_stream(
        self,
        state: MowerState,
        reason: str,
        *,
        force: bool = False,
    ) -> MowerState:
        """Ask ECOVACS for the app map view, which triggers fast onPos pushes."""
        if not force and state.activity not in LIVE_POSITION_STREAM_ACTIVITIES:
            self._capture_event(
                "live_position_stream_request_skipped",
                {
                    "reason": reason,
                    "cause": "not_in_live_stream_activity",
                    "activity": state.activity.value
                    if state.activity is not None
                    else None,
                },
            )
            return state

        await self._async_keep_app_presence_mqtt(reason)

        now = monotonic()
        if (
            not force
            and self._last_live_position_stream_request_at is not None
            and now - self._last_live_position_stream_request_at
            < LIVE_POSITION_STREAM_REQUEST_MIN_INTERVAL_SECONDS
        ):
            self._capture_event(
                "live_position_stream_request_skipped",
                {
                    "reason": reason,
                    "cause": "recently_requested",
                    "min_interval_seconds": LIVE_POSITION_STREAM_REQUEST_MIN_INTERVAL_SECONDS,
                },
            )
            return state

        self._last_live_position_stream_request_at = now
        state = await self._async_refresh_live_position(state)
        mid = state.map.mid
        if not mid:
            self._capture_event(
                "live_position_stream_request_skipped",
                {"reason": reason, "cause": "missing_mid"},
            )
            return state

        await self.api.control(
            self.device,
            "getMapSet_V2",
            self._app_live_map_payload(mid, "ar"),
        )
        previous_state = state
        response = await self.api.control(
            self.device,
            "getMapTrace_V2",
            self._app_live_map_payload(mid, MAP_TRACE_TYPE),
        )
        state = apply_response(state, "getMapTrace_V2", response)
        trace_changed = self._trace_path_changed(previous_state, state)
        if trace_changed:
            state = self._reset_live_position_segment(state)
            self._mark_trace_mqtt_applied()

        for map_type in APP_LIVE_MAP_TYPES[1:]:
            await self.api.control(
                self.device,
                "getMapSet_V2",
                self._app_live_map_payload(mid, map_type),
            )
        await self.api.control(
            self.device,
            "getMapPoint",
            {"mid": mid, "bdTaskID": self._next_app_bd_task_id()},
        )
        self._capture_event(
            "live_position_stream_requested",
            {
                "reason": reason,
                "force": force,
                "mid": mid,
                "map_types": APP_LIVE_MAP_TYPES,
                "trace_changed": trace_changed,
            },
        )
        return state

    async def _async_keep_app_presence_mqtt(self, reason: str) -> None:
        """Keep the captured official-app presence session alive for visible cards."""
        self._app_presence_stop_at = monotonic() + APP_PRESENCE_MQTT_TTL_SECONDS
        try:
            await self._app_presence_mqtt.start()
        except Exception as err:  # noqa: BLE001 - experimental side channel only
            _LOGGER.warning("ECOVACS app-presence MQTT start failed: %s", err)
            self._capture_event(
                "app_presence_mqtt_start_error",
                {"reason": reason, "exception": repr(err)},
            )
            return

        self._capture_event(
            "app_presence_mqtt_keepalive",
            {
                "reason": reason,
                "ttl_seconds": APP_PRESENCE_MQTT_TTL_SECONDS,
            },
        )
        if (
            self._app_presence_stop_task is None
            or self._app_presence_stop_task.done()
        ):
            self._app_presence_stop_task = self._create_background_task(
                self._async_stop_app_presence_mqtt_when_idle(),
                "ecovacs_goat_app_presence_mqtt_stop",
            )

    async def _async_stop_app_presence_mqtt_when_idle(self) -> None:
        """Stop the app-presence MQTT session after card requests stop arriving."""
        try:
            while self._app_presence_stop_at is not None:
                delay = self._app_presence_stop_at - monotonic()
                if delay <= 0:
                    break
                await asyncio.sleep(delay)
            self._app_presence_stop_at = None
            await self._app_presence_mqtt.stop()
        except asyncio.CancelledError:
            raise
        finally:
            if self._app_presence_stop_task is asyncio.current_task():
                self._app_presence_stop_task = None

    def _app_live_map_payload(self, mid: str, map_type: str) -> dict[str, str]:
        """Return the captured app-style map-view command body."""
        return {
            "mid": mid,
            "type": map_type,
            "bdTaskID": self._next_app_bd_task_id(),
        }

    def _next_app_bd_task_id(self) -> str:
        """Return an app-like per-request id used in captured map-view calls."""
        self._live_map_request_counter = (self._live_map_request_counter + 1) % 1000
        return f"{int(time() * 1000)}{self._live_map_request_counter:03d}"

    def _schedule_outcome_poll(
        self,
        key: str,
        predicate: Callable[[MowerState], bool],
        *,
        timeout: int = COMMAND_VERIFY_TIMEOUT_SECONDS,
        interval: int = COMMAND_VERIFY_INTERVAL_SECONDS,
        initial_delay: int = COMMAND_VERIFY_INITIAL_DELAY_SECONDS,
    ) -> None:
        """Verify a command outcome with bounded readback polling."""
        existing = self._outcome_refresh_tasks.get(key)
        if existing and not existing.done():
            existing.cancel()
        self._outcome_refresh_tasks[key] = self._create_background_task(
            self._async_poll_until_outcome(
                key,
                predicate,
                timeout=timeout,
                interval=interval,
                initial_delay=initial_delay,
            ),
            f"ecovacs_goat_outcome_{key}",
        )

    async def _async_poll_until_outcome(
        self,
        key: str,
        predicate: Callable[[MowerState], bool],
        *,
        timeout: int,
        interval: int,
        initial_delay: int,
    ) -> None:
        """Poll grouped state until the expected command result is observed."""
        try:
            await asyncio.sleep(initial_delay)
            deadline = monotonic() + timeout
            while monotonic() <= deadline:
                state = await self._async_refresh_state_groups()
                self.async_set_updated_data(state)
                if predicate(state):
                    self._capture_event(
                        "command_outcome_confirmed",
                        {
                            "key": key,
                            "activity": state.activity.value
                            if state.activity is not None
                            else None,
                        },
                    )
                    return
                await asyncio.sleep(interval)
            _LOGGER.debug("ECOVACS command outcome %s was not confirmed in time", key)
            self._capture_event("command_outcome_timeout", {"key": key})
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("ECOVACS command outcome poll %s failed", key, exc_info=True)
            self._capture_event(
                "command_outcome_error",
                {"key": key, "exception": repr(err)},
            )
        finally:
            if self._outcome_refresh_tasks.get(key) is asyncio.current_task():
                self._outcome_refresh_tasks.pop(key, None)

    async def async_refresh_if_stale(self) -> None:
        """Refresh grouped state if MQTT/readback data is stale."""
        if self._has_fresh_state():
            return
        _LOGGER.debug(
            "Refreshing ECOVACS mower state before command because live updates are stale"
        )
        self.async_set_updated_data(await self._async_refresh_state_groups())

    async def async_refresh_state(self) -> None:
        """Force a grouped state refresh from the mower."""
        self.async_set_updated_data(await self._async_refresh_state_groups())

    async def _async_refresh_state_groups(self) -> MowerState:
        """Refresh only the app-captured grouped state/settings payloads."""
        state = self.data or MowerState()
        for group in STARTUP_GET_INFO_GROUPS:
            response = await self.api.control(self.device, "getInfo", list(group))
            state = apply_response(state, "getInfo", response)
        state = await self._async_refresh_live_map(state)
        self._last_readback_at = monotonic()
        return state

    async def _async_refresh_live_map(self, state: MowerState) -> MowerState:
        """Refresh live map position and request a map trace push."""
        try:
            state = await self._async_refresh_live_position(state)
            if state.map.mid:
                if not state.map.info.outline:
                    map_info_payload: dict[str, Any] = {
                        "using": 0,
                        "serial": 0,
                        "index": 0,
                        "type": MAP_TRACE_TYPE,
                    }
                    if state.task_id:
                        map_info_payload["bdTaskID"] = state.task_id
                    response = await self.api.control(
                        self.device,
                        "getMapInfo_V2",
                        map_info_payload,
                    )
                    state = apply_response(state, "getMapInfo_V2", response)
                response = await self.api.control(
                    self.device,
                    "getMapTrace_V2",
                    {"mid": state.map.mid, "type": MAP_TRACE_TYPE},
                )
                state = apply_response(state, "getMapTrace_V2", response)
        except EcovacsApiError as err:
            _LOGGER.debug("ECOVACS live map refresh failed: %s", err)
        return state

    async def _async_refresh_live_position(self, state: MowerState) -> MowerState:
        """Refresh the mower, charger, and beacon positions."""
        response = await self.api.control(
            self.device, "getPos", ["chargePos", "deebotPos", "uwbPos"]
        )
        return apply_response(state, "getPos", response)

    async def _async_refresh_live_map_after_mqtt_start(self) -> None:
        """Request live map data after MQTT has had time to subscribe."""
        for delay in (5, 10):
            await asyncio.sleep(delay)
            self.async_set_updated_data(
                await self._async_refresh_live_map(self.data or MowerState())
            )
            if self.data and self.data.map.trace.path:
                return

    def _has_fresh_state(self) -> bool:
        """Return whether MQTT or a recent readback has fresh state."""
        last_seen = max(
            (stamp for stamp in (self._last_mqtt_at, self._last_readback_at) if stamp),
            default=None,
        )
        return last_seen is not None and monotonic() - last_seen <= FRESH_STATE_SECONDS

    def _capture_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Write a coordinator capture event if capture is active."""
        if self._debug_capture is None:
            return
        self._debug_capture.capture_event(
            event_type,
            {
                "device": {
                    "did": self.device.did,
                    "class": self.device.device_class,
                    "resource": self.device.resource,
                    "model": self.device.model,
                },
                **data,
            },
        )

    def _create_background_task(
        self, coro: Coroutine[Any, Any, None], name: str
    ) -> asyncio.Task[None]:
        """Create a non-startup-blocking task where supported by Home Assistant."""
        if hasattr(self.hass, "async_create_background_task"):
            return self.hass.async_create_background_task(coro, name)
        return asyncio.create_task(coro, name=name)

    async def control(
        self,
        command: str,
        data: Any | None = None,
        *,
        refresh_if_stale: bool = True,
    ) -> None:
        """Execute a command and merge the response into the cache."""
        if refresh_if_stale:
            await self.async_refresh_if_stale()
        response = await self.api.control(
            self.device, command, self._command_payload(command, data or {})
        )
        self.async_set_updated_data(apply_response(self.data, command, response))

    async def start_mowing(self) -> None:
        """Start or resume mowing using app-captured clean_V2 bodies."""
        await self.async_refresh_if_stale()
        previous_activity = self.data.activity
        if previous_activity in {MowerActivity.PAUSED, MowerActivity.RETURNING}:
            payload = {"act": "resume"}
        else:
            payload = {"act": "start", "content": {"type": "auto"}}
        await self.control("clean_V2", payload, refresh_if_stale=False)
        mower_map = self.data.map
        if previous_activity not in {
            MowerActivity.MOWING,
            MowerActivity.PAUSED,
            MowerActivity.RETURNING,
        }:
            mower_map = replace(mower_map, position_history=())
        self.async_set_updated_data(
            replace(
                self.data,
                activity=MowerActivity.MOWING,
                map=mower_map,
            )
        )
        self._ensure_mowing_position_refresh()
        self._schedule_outcome_poll(
            "start_mowing", lambda state: state.activity is MowerActivity.MOWING
        )

    async def pause(self) -> None:
        """Pause active mowing."""
        await self.async_refresh_if_stale()
        await self.control("clean_V2", {"act": "pause"}, refresh_if_stale=False)
        self.async_set_updated_data(replace(self.data, activity=MowerActivity.PAUSED))
        self._schedule_outcome_poll(
            "pause", lambda state: state.activity is MowerActivity.PAUSED
        )

    async def end_mowing(self) -> None:
        """End the active mowing session."""
        await self.async_refresh_if_stale()
        await self.control(
            "clean_V2",
            {"act": "stop", "content": {"type": ""}},
            refresh_if_stale=False,
        )
        self.async_set_updated_data(replace(self.data, activity=MowerActivity.IDLE))
        self._schedule_outcome_poll(
            "end_mowing",
            lambda state: state.activity in {MowerActivity.IDLE, MowerActivity.DOCKED},
        )

    async def dock(self) -> None:
        """Return to charge, or cancel an active return-to-charge command."""
        await self.async_refresh_if_stale()
        if self.data.activity is MowerActivity.RETURNING:
            await self.control("charge", {"act": "stop"}, refresh_if_stale=False)
            self.async_set_updated_data(replace(self.data, activity=MowerActivity.PAUSED))
            self._schedule_outcome_poll(
                "dock",
                lambda state: state.activity
                in {MowerActivity.PAUSED, MowerActivity.MOWING, MowerActivity.IDLE},
            )
            return

        await self.control("charge", {"act": "go"}, refresh_if_stale=False)
        self.async_set_updated_data(replace(self.data, activity=MowerActivity.RETURNING))
        self._schedule_outcome_poll(
            "dock",
            lambda state: state.activity is MowerActivity.DOCKED,
            timeout=240,
            interval=RETURNING_REFRESH_SECONDS,
        )
        self._ensure_returning_refresh()

    def _command_payload(self, command: str, data: Any) -> Any:
        """Add the current app task id to write payloads when known."""
        if (
            command not in COMMANDS_WITH_TASK_ID
            or not isinstance(data, dict)
            or not self.data.task_id
            or "bdTaskID" in data
        ):
            return data
        return {**data, "bdTaskID": self.data.task_id}

    async def set_enabled(self, key: str, enabled: bool) -> None:
        """Set a boolean mower setting."""
        await self.async_refresh_if_stale()
        settings = self.data.settings
        match key:
            case "rain_sensor":
                await self.control(
                    "setRainDelay",
                    {"enable": 1 if enabled else 0, "delay": settings.rain_delay or 180},
                    refresh_if_stale=False,
                )
                state = apply_command_data(
                    self.data,
                    "getRainDelay",
                    {"enable": 1 if enabled else 0, "delay": settings.rain_delay or 180},
                )
                predicate = lambda state: state.settings.rain_enabled is enabled
            case "animal_protection":
                await self.control(
                    "setAnimProtect",
                    {
                        "enable": 1 if enabled else 0,
                        "start": settings.animal_start or "19:00",
                        "end": settings.animal_end or "08:00",
                    },
                    refresh_if_stale=False,
                )
                state = apply_command_data(
                    self.data,
                    "getAnimProtect",
                    {
                        "enable": 1 if enabled else 0,
                        "start": settings.animal_start or "19:00",
                        "end": settings.animal_end or "08:00",
                    },
                )
                predicate = lambda state: state.settings.animal_enabled is enabled
            case "ai_recognition":
                await self.control(
                    "setRecognization",
                    {"state": 1 if enabled else 0},
                    refresh_if_stale=False,
                )
                state = apply_command_data(
                    self.data, "getRecognization", {"state": 1 if enabled else 0}
                )
                predicate = lambda state: state.settings.ai_recognition is enabled
            case "border_switch":
                await self.control(
                    "setBorderSwitch",
                    {"enable": 1 if enabled else 0},
                    refresh_if_stale=False,
                )
                state = apply_command_data(
                    self.data,
                    "getBorderSwitch",
                    {"enable": 1 if enabled else 0, "mode": settings.border_mode or 0},
                )
                predicate = lambda state: state.settings.border_switch is enabled
            case "safer_mode":
                await self.control(
                    "setChildLock",
                    {"on": 1 if enabled else 0},
                    refresh_if_stale=False,
                )
                state = apply_command_data(
                    self.data, "getChildLock", {"on": 1 if enabled else 0}
                )
                predicate = lambda state: state.settings.safer_mode is enabled
            case "move_up_warning":
                await self.control(
                    "setMoveupWarning",
                    {"enable": 1 if enabled else 0},
                    refresh_if_stale=False,
                )
                state = apply_command_data(
                    self.data, "getMoveupWarning", {"enable": 1 if enabled else 0}
                )
                predicate = lambda state: state.settings.move_up_warning is enabled
            case "cross_map_border_warning":
                await self.control(
                    "setCrossMapBorderWarning",
                    {"enable": 1 if enabled else 0},
                    refresh_if_stale=False,
                )
                state = apply_command_data(
                    self.data,
                    "getCrossMapBorderWarning",
                    {"enable": 1 if enabled else 0},
                )
                predicate = (
                    lambda state: state.settings.cross_map_border_warning is enabled
                )
            case _:
                raise ValueError(f"Unsupported switch key {key}")
        self.async_set_updated_data(state)
        self._schedule_outcome_poll(f"set_{key}", predicate)

    async def set_rain_delay(self, delay: int) -> None:
        """Set rain delay in minutes."""
        await self.async_refresh_if_stale()
        enabled = self.data.settings.rain_enabled
        await self.control(
            "setRainDelay",
            {"enable": 1 if enabled else 0, "delay": delay},
            refresh_if_stale=False,
        )
        self.async_set_updated_data(
            apply_command_data(
                self.data, "getRainDelay", {"enable": 1 if enabled else 0, "delay": delay}
            )
        )
        self._schedule_outcome_poll(
            "set_rain_delay", lambda state: state.settings.rain_delay == delay
        )

    async def set_cut_direction(self, angle: int) -> None:
        """Set mowing cut direction.

        The mower silently ignores ``setCutDirection`` while a job is running,
        so refuse the call up-front from any source (UI slider, custom card,
        services, scripts) when the activity indicates active work.
        """
        activity = self.data.activity if self.data else None
        if activity in CUT_DIRECTION_LOCKED_ACTIVITIES:
            raise HomeAssistantError(
                "Cut direction can only be changed while the mower is idle or "
                "docked. End or pause-and-end the current job first."
            )
        await self.async_refresh_if_stale()
        await self.control(
            "setCutDirection", {"angle": angle}, refresh_if_stale=False
        )
        self.async_set_updated_data(
            apply_command_data(self.data, "getCutDirection", {"angle": angle})
        )
        self._schedule_outcome_poll(
            "set_cut_direction", lambda state: state.settings.cut_direction == angle
        )

    async def set_mowing_efficiency(self, option: str) -> None:
        """Set mowing efficiency."""
        await self.async_refresh_if_stale()
        level = MOWING_EFFICIENCY_LEVELS[option]
        await self.control(
            "setCutEfficiency", {"level": level}, refresh_if_stale=False
        )
        self.async_set_updated_data(
            apply_command_data(self.data, "getCutEfficiency", {"level": level})
        )
        self._schedule_outcome_poll(
            "set_mowing_efficiency",
            lambda state: state.settings.mowing_efficiency == option,
        )

    async def set_obstacle_avoidance(self, option: str) -> None:
        """Set obstacle avoidance mode."""
        await self.async_refresh_if_stale()
        level = OBSTACLE_AVOIDANCE_LEVELS[option]
        await self.control(
            "setObstacleHeight", {"level": level}, refresh_if_stale=False
        )
        self.async_set_updated_data(
            apply_command_data(self.data, "getObstacleHeight", {"level": level})
        )
        self._schedule_outcome_poll(
            "set_obstacle_avoidance",
            lambda state: state.settings.obstacle_avoidance == option,
        )

    async def set_animal_time(self, key: str, value: str) -> None:
        """Set animal protection time window."""
        await self.async_refresh_if_stale()
        settings = self.data.settings
        start = value if key == "animal_start" else settings.animal_start or "19:00"
        end = value if key == "animal_end" else settings.animal_end or "08:00"
        enabled = settings.animal_enabled
        await self.control(
            "setAnimProtect",
            {"enable": 1 if enabled else 0, "start": start, "end": end},
            refresh_if_stale=False,
        )
        self.async_set_updated_data(
            apply_command_data(
                self.data,
                "getAnimProtect",
                {"enable": 1 if enabled else 0, "start": start, "end": end},
            )
        )
        self._schedule_outcome_poll(
            f"set_{key}",
            lambda state: (
                state.settings.animal_start if key == "animal_start" else state.settings.animal_end
            )
            == value,
        )
