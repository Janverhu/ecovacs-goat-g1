"""Models for the ECOVACS GOAT mower driver."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MowerActivity(StrEnum):
    """Internal mower activity values."""

    IDLE = "idle"
    MOWING = "mowing"
    PAUSED = "paused"
    RETURNING = "returning"
    DOCKED = "docked"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MowerDevice:
    """Device details required by ECOVACS N-GIoT and MQTT."""

    did: str
    device_class: str
    resource: str
    name: str
    model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "MowerDevice":
        """Create a mower device from an ECOVACS device-list entry."""
        name = data.get("nick") or data.get("name") or data.get("deviceName") or "Mower"
        return cls(
            did=data["did"],
            device_class=data["class"],
            resource=data["resource"],
            name=name,
            model=data.get("deviceName"),
            raw=dict(data),
        )


@dataclass(frozen=True)
class NetworkInfo:
    """Network diagnostic state."""

    ip: str | None = None
    ssid: str | None = None
    rssi: int | None = None
    mac: str | None = None


@dataclass(frozen=True)
class MowerSettings:
    """Mower configuration values."""

    rain_enabled: bool | None = None
    rain_delay: int | None = None
    animal_enabled: bool | None = None
    animal_start: str | None = None
    animal_end: str | None = None
    ai_recognition: bool | None = None
    border_switch: bool | None = None
    border_mode: int | None = None
    safer_mode: bool | None = None
    move_up_warning: bool | None = None
    cross_map_border_warning: bool | None = None
    cut_direction: int | None = None
    mowing_efficiency: str | None = None
    obstacle_avoidance: str | None = None


@dataclass(frozen=True)
class MowerStats:
    """Mower statistics."""

    area: int | None = None
    job_area: int | None = None
    progress: float | None = None
    duration: int | None = None
    total_area: int | None = None
    total_duration: int | None = None
    total_count: int | None = None


@dataclass(frozen=True)
class MowerState:
    """Complete cached mower state used by Home Assistant entities."""

    available: bool = True
    activity: MowerActivity = MowerActivity.UNKNOWN
    task_id: str | None = None
    battery: int | None = None
    charging: bool | None = None
    charge_mode: str | None = None
    error_code: int | None = None
    error_description: str | None = None
    network: NetworkInfo = field(default_factory=NetworkInfo)
    settings: MowerSettings = field(default_factory=MowerSettings)
    stats: MowerStats = field(default_factory=MowerStats)
    lifespans: dict[str, float] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
