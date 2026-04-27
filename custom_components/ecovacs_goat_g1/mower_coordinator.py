"""Coordinator for ECOVACS GOAT mower entities."""

from __future__ import annotations

from dataclasses import replace
import logging
from time import monotonic
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .mower_api import EcovacsApiError, EcovacsMowerApi
from .mower_messages import (
    MOWING_EFFICIENCY_LEVELS,
    OBSTACLE_AVOIDANCE_LEVELS,
    apply_command_data,
    apply_mqtt_payload,
    apply_response,
)
from .mower_models import MowerActivity, MowerDevice, MowerState
from .mower_mqtt import MowerMqttClient

_LOGGER = logging.getLogger(__name__)
FRESH_STATE_SECONDS = 300
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


class MowerCoordinator(DataUpdateCoordinator[MowerState]):
    """Data coordinator for one mower."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: EcovacsMowerApi,
        device: MowerDevice,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"ECOVACS GOAT {device.did}",
        )
        self.api = api
        self.device = device
        self.data = MowerState()
        self._last_mqtt_at: float | None = None
        self._last_readback_at: float | None = None
        self._mqtt = MowerMqttClient(
            api,
            device,
            hass.loop,
            self._handle_mqtt_message,
        )

    async def async_start(self) -> None:
        """Start push subscription after initial state refresh."""
        await self.async_config_entry_first_refresh()
        await self._mqtt.start()

    async def async_stop(self) -> None:
        """Stop push subscription."""
        await self._mqtt.stop()

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
        try:
            self.async_set_updated_data(apply_mqtt_payload(self.data, topic, payload))
            self._last_mqtt_at = monotonic()
        except Exception:
            _LOGGER.exception("Failed to parse ECOVACS MQTT message %s", topic)

    async def async_refresh_if_stale(self) -> None:
        """Refresh grouped state if MQTT/readback data is stale."""
        if self._has_fresh_state():
            return
        _LOGGER.debug(
            "Refreshing ECOVACS mower state before command because live updates are stale"
        )
        self.async_set_updated_data(await self._async_refresh_state_groups())

    async def _async_refresh_state_groups(self) -> MowerState:
        """Refresh only the app-captured grouped state/settings payloads."""
        state = self.data or MowerState()
        for group in STARTUP_GET_INFO_GROUPS:
            response = await self.api.control(self.device, "getInfo", list(group))
            state = apply_response(state, "getInfo", response)
        self._last_readback_at = monotonic()
        return state

    def _has_fresh_state(self) -> bool:
        """Return whether MQTT or a recent readback has fresh state."""
        last_seen = max(
            (stamp for stamp in (self._last_mqtt_at, self._last_readback_at) if stamp),
            default=None,
        )
        return last_seen is not None and monotonic() - last_seen <= FRESH_STATE_SECONDS

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
        if self.data.activity in {MowerActivity.PAUSED, MowerActivity.RETURNING}:
            payload = {"act": "resume"}
        else:
            payload = {"act": "start", "content": {"type": "auto"}}
        await self.control("clean_V2", payload, refresh_if_stale=False)
        self.async_set_updated_data(replace(self.data, activity=MowerActivity.MOWING))

    async def pause(self) -> None:
        """Pause active mowing."""
        await self.async_refresh_if_stale()
        await self.control("clean_V2", {"act": "pause"}, refresh_if_stale=False)
        self.async_set_updated_data(replace(self.data, activity=MowerActivity.PAUSED))

    async def end_mowing(self) -> None:
        """End the active mowing session."""
        await self.async_refresh_if_stale()
        await self.control(
            "clean_V2",
            {"act": "stop", "content": {"type": ""}},
            refresh_if_stale=False,
        )
        self.async_set_updated_data(replace(self.data, activity=MowerActivity.IDLE))

    async def dock(self) -> None:
        """Return to charge."""
        await self.async_refresh_if_stale()
        await self.control("charge", {"act": "go"}, refresh_if_stale=False)
        self.async_set_updated_data(replace(self.data, activity=MowerActivity.RETURNING))

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
            case "ai_recognition":
                await self.control(
                    "setRecognization",
                    {"state": 1 if enabled else 0},
                    refresh_if_stale=False,
                )
                state = apply_command_data(
                    self.data, "getRecognization", {"state": 1 if enabled else 0}
                )
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
            case "safer_mode":
                await self.control(
                    "setChildLock",
                    {"on": 1 if enabled else 0},
                    refresh_if_stale=False,
                )
                state = apply_command_data(
                    self.data, "getChildLock", {"on": 1 if enabled else 0}
                )
            case "move_up_warning":
                await self.control(
                    "setMoveupWarning",
                    {"enable": 1 if enabled else 0},
                    refresh_if_stale=False,
                )
                state = apply_command_data(
                    self.data, "getMoveupWarning", {"enable": 1 if enabled else 0}
                )
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
            case _:
                raise ValueError(f"Unsupported switch key {key}")
        self.async_set_updated_data(state)

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

    async def set_cut_direction(self, angle: int) -> None:
        """Set mowing cut direction."""
        await self.async_refresh_if_stale()
        await self.control(
            "setCutDirection", {"angle": angle}, refresh_if_stale=False
        )
        self.async_set_updated_data(
            apply_command_data(self.data, "getCutDirection", {"angle": angle})
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
