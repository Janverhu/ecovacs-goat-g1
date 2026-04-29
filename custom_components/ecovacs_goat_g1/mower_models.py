"""Models for the ECOVACS GOAT mower driver."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

POSITION_HISTORY_ATTRIBUTE_POINTS = 800
POSITION_HISTORY_DENSE_TAIL_POINTS = 600
TRACE_ATTRIBUTE_POINTS = 120
OUTLINE_ATTRIBUTE_POINTS = 100
OBSTACLE_ATTRIBUTE_POINTS = 24


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
class MapPosition:
    """Position on the mower map coordinate plane."""

    x: int
    y: int
    a: int | None = None
    invalid: int | None = None
    sn: str | None = None
    z: int | None = None
    t: int | None = None

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "MapPosition | None":
        """Create a map position from an ECOVACS payload item."""
        if data.get("x") is None or data.get("y") is None:
            return None
        try:
            return cls(
                x=int(data["x"]),
                y=int(data["y"]),
                a=int(data["a"]) if data.get("a") is not None else None,
                invalid=int(data["invalid"]) if data.get("invalid") is not None else None,
                sn=str(data["sn"]) if data.get("sn") is not None else None,
                z=int(data["z"]) if data.get("z") is not None else None,
                t=int(data["t"]) if data.get("t") is not None else None,
            )
        except (TypeError, ValueError):
            return None

    def as_dict(self) -> dict[str, Any]:
        """Return a compact serialisable representation."""
        return {
            key: value
            for key, value in {
                "x": self.x,
                "y": self.y,
                "a": self.a,
                "invalid": self.invalid,
                "sn": self.sn,
                "z": self.z,
                "t": self.t,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class MowerMapTrace:
    """Chunked live map trace payload pushed by the mower."""

    batch_id: str | None = None
    serial: str | None = None
    info_size: int | None = None
    type: str | None = None
    chunks: dict[int, str] = field(default_factory=dict)
    path: tuple[MapPosition, ...] = ()

    @property
    def complete(self) -> bool:
        """Return whether the received chunks look contiguous."""
        if self.path:
            return True
        if not self.chunks:
            return False
        indexes = sorted(self.chunks)
        return indexes == list(range(indexes[-1] + 1))

    def as_dict(self) -> dict[str, Any]:
        """Return serialisable trace metadata and chunks."""
        return {
            "batch_id": self.batch_id,
            "serial": self.serial,
            "type": self.type,
            "info_size": self.info_size,
            "complete": self.complete,
            "chunk_count": len(self.chunks),
            "chunk_indexes": sorted(self.chunks),
            "path": [
                position.as_dict()
                for position in _sample_positions(self.path, TRACE_ATTRIBUTE_POINTS)
            ],
        }


@dataclass(frozen=True)
class MowerMapInfo:
    """Chunked base map payload pushed by the mower."""

    batch_id: str | None = None
    serial: str | None = None
    info_size: int | None = None
    type: str | None = None
    chunks: dict[int, str] = field(default_factory=dict)
    outline: tuple[MapPosition, ...] = ()
    obstacles: tuple[tuple[MapPosition, ...], ...] = ()

    @property
    def complete(self) -> bool:
        """Return whether the base map has decoded geometry."""
        return bool(self.outline)

    def as_dict(self) -> dict[str, Any]:
        """Return serialisable map geometry."""
        return {
            "batch_id": self.batch_id,
            "serial": self.serial,
            "type": self.type,
            "info_size": self.info_size,
            "complete": self.complete,
            "chunk_count": len(self.chunks),
            "chunk_indexes": sorted(self.chunks),
            "outline": [
                position.as_dict()
                for position in _sample_positions(self.outline, OUTLINE_ATTRIBUTE_POINTS)
            ],
            "obstacles": [
                [
                    position.as_dict()
                    for position in _sample_positions(
                        obstacle, OBSTACLE_ATTRIBUTE_POINTS
                    )
                ]
                for obstacle in self.obstacles
            ],
        }


@dataclass(frozen=True)
class MowerMap:
    """Live map data used by the Lovelace card."""

    mid: str | None = None
    current_position: MapPosition | None = None
    charge_positions: tuple[MapPosition, ...] = ()
    uwb_positions: tuple[MapPosition, ...] = ()
    position_history: tuple[MapPosition, ...] = ()
    info: MowerMapInfo = field(default_factory=MowerMapInfo)
    trace: MowerMapTrace = field(default_factory=MowerMapTrace)
    last_update_ts: int | None = None
    revision: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Return a serialisable map snapshot."""
        return {
            "mid": self.mid,
            "current_position": self.current_position.as_dict()
            if self.current_position
            else None,
            "charge_positions": [position.as_dict() for position in self.charge_positions],
            "uwb_positions": [position.as_dict() for position in self.uwb_positions],
            "position_history": [
                position.as_dict()
                for position in _sample_positions(
                    self.position_history,
                    POSITION_HISTORY_ATTRIBUTE_POINTS,
                    dense_tail=POSITION_HISTORY_DENSE_TAIL_POINTS,
                )
            ],
            "info": self.info.as_dict(),
            "trace": self.trace.as_dict(),
            "last_update_ts": self.last_update_ts,
            "revision": self.revision,
        }


def _sample_positions(
    positions: tuple[MapPosition, ...], max_points: int, *, dense_tail: int = 0
) -> tuple[MapPosition, ...]:
    """Return a bounded path for Home Assistant state attributes.

    Keep the recent tail dense for live movement so the card does not suddenly
    lose the newest path shape when the full history grows beyond the limit.
    """
    if len(positions) <= max_points:
        return positions
    if dense_tail > 0:
        tail_size = min(dense_tail, max_points)
        head_limit = max_points - tail_size
        tail = positions[-tail_size:]
        head = positions[:-tail_size]
        if head_limit <= 0:
            return tail
        sampled_head = _sample_positions(head, head_limit)
        return (*sampled_head, *tail)
    step = max(1, (len(positions) + max_points - 1) // max_points)
    sampled = positions[::step]
    if sampled[-1] != positions[-1]:
        sampled = (*sampled, positions[-1])
    return sampled


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
    map: MowerMap = field(default_factory=MowerMap)
    lifespans: dict[str, float] = field(default_factory=dict)
    robot_features: dict[str, Any] | None = None
    goat_g1_variant: str = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)
