"""Per-model capability profiles for GOAT mowers.

The integration was built and validated against the **GOAT G1-800** (UWB beacons,
``*_V2`` map dialect). Users run other GOAT models too; the closed issues on the
project tracker show O-series mowers (O800 RTK, O1200, O1200 LiDAR Pro) where the
map and some controls behave differently.

The O-series differences below were confirmed against a decrypted capture of a
**GOAT O800 RTK** (device class ``9bts2s``, model ``GOAT_O800_LC``, firmware
``1.9.10``). They differ from the G1 line in a few concrete, important ways:

============  ===========================  ==============================
Aspect        GOAT G1 line                 GOAT O-series (O800 RTK)
============  ===========================  ==============================
Clean cmd     ``clean_V2``                 ``clean``
Stop body     ``content.type = ""``        ``content.type = "auto"``
Clean info    ``getCleanInfo_V2``          ``getCleanInfo`` (same fields)
Position      ``deebotPos/chargePos/uwb``  ``deebotPos/chargePos/rtkPos``
Map dialect   ``getMapInfo_V2`` /          ``getMapState`` / ``getMI`` /
              ``getMapTrace_V2``           ``getMapTrack`` / ``getAreaSet``
============  ===========================  ==============================

Dock (``charge {act:"go"}``), ``appping``, ``getLifeSpan``, battery, error, and
the ``getCleanInfo`` *status fields* (``state`` / ``cleanState.motionState`` /
``trigger`` / ``cid``) are shared, so the existing status parser handles both.

The O-series lawn outline arrives as ``onMI`` pushes and decodes from a
direction chain (O1200 LiDAR Pro) or a point list (O800 RTK). ``getAreaSet``
decodes to area anchors. The ``getMI`` request body that triggers the push is
not captured yet, so the coordinator tries candidate bodies while the outline
is missing. O-series support remains **experimental / best-effort**.

The O1200 LiDAR Pro also runs TrueEdge edge trims as their own ``clean`` job
type, ``borderrotate`` (captured on class ``2i0fns``, firmware ``1.15.16``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from .goat_g1_models import (
    FAMILY_G1,
    FAMILY_O_SERIES,
    FAMILY_UNKNOWN,
    VARIANT_O1200_LIDAR_PRO,
    classify_goat_g1_variant,
    variant_family,
)

EDGE_TRIM_TASK_TYPE = "borderrotate"
# The app sent this value when starting a trim on a single-lawn map.
_EDGE_TRIM_START_VALUE = "reid:1"
_EDGE_TRIM_VARIANTS = frozenset({VARIANT_O1200_LIDAR_PRO})


class MowerFamily(StrEnum):
    """Coarse GOAT protocol family."""

    GOAT_G1 = FAMILY_G1
    GOAT_O_SERIES = FAMILY_O_SERIES
    UNKNOWN = FAMILY_UNKNOWN


class MapDialect(StrEnum):
    """Map command dialect a mower understands."""

    MAP_V2 = "map_v2"
    MAP_RTK = "map_rtk"


@dataclass(frozen=True)
class CapabilityProfile:
    """Static, per-model protocol capabilities.

    These values seed the coordinator's runtime
    :class:`~.mower_compat.ProtocolProfile` and pick the command dialect. They
    never *enable* a command a mower rejects; the runtime profile still adapts on
    failures. They only avoid issuing commands a family is known not to speak.
    """

    family: MowerFamily
    map_dialect: MapDialect
    map_uses_v2: bool
    # N-GIoT command used to start/pause/resume/stop mowing.
    clean_command: str
    # ``content.type`` sent with a clean "stop" (G1 uses "", O-series "auto").
    stop_content_type: str
    # Whether every clean act carries ``content:{type:"auto"}`` (O-series) or
    # only start/stop do (G1 line).
    clean_always_content: bool
    # Grouped getInfo key used to read mowing status.
    clean_info_command: str
    # Grouped getInfo key for scheduled jobs. The O1200 LiDAR Pro reports a
    # scheduled mow only there while getCleanInfo stays "idle".
    schedule_info_command: str | None
    # Fields requested via getPos (UWB vs RTK reference points).
    position_fields: tuple[str, ...]
    # The G1 app lists a camera lens brush. O-series getLifeSpan replies have
    # none: blade only on the O800 RTK, and the app asks the O1200 LiDAR Pro
    # for blade, weedRope and trimmerBrush.
    has_lens_brush: bool
    # Whether the mower has a TrueEdge trimmer (``borderrotate`` jobs).
    edge_trim: bool
    experimental: bool
    label: str

    def clean_body(self, act: str, task_type: str | None = None) -> dict[str, Any]:
        """Return the ``clean`` / ``clean_V2`` body for a mowing action.

        ``act`` is one of ``start`` / ``resume`` / ``pause`` / ``stop``.
        ``task_type`` is the running job's type. The app pauses, resumes and
        stops a TrueEdge trim by that type instead of ``auto``.
        """
        if self.edge_trim and task_type == EDGE_TRIM_TASK_TYPE and act != "start":
            return {"act": act, "content": {"type": EDGE_TRIM_TASK_TYPE}}
        if self.clean_always_content:
            return {"act": act, "content": {"type": "auto"}}
        if act == "start":
            return {"act": "start", "content": {"type": "auto"}}
        if act == "stop":
            return {"act": "stop", "content": {"type": self.stop_content_type}}
        return {"act": act}

    def edge_trim_body(self) -> dict[str, Any]:
        """Return the ``clean`` body that starts a TrueEdge trim."""
        return {
            "act": "start",
            "content": {"type": EDGE_TRIM_TASK_TYPE, "value": _EDGE_TRIM_START_VALUE},
        }

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot for diagnostics."""
        return {
            "family": str(self.family),
            "map_dialect": str(self.map_dialect),
            "map_uses_v2": self.map_uses_v2,
            "clean_command": self.clean_command,
            "clean_info_command": self.clean_info_command,
            "schedule_info_command": self.schedule_info_command,
            "position_fields": list(self.position_fields),
            "has_lens_brush": self.has_lens_brush,
            "edge_trim": self.edge_trim,
            "experimental": self.experimental,
            "label": self.label,
        }


# The G1 profile is the original, validated behaviour. UNKNOWN intentionally maps
# to the G1 profile so previously-working setups never regress: the runtime
# profile still adapts on failures, exactly as before this change.
_G1_PROFILE = CapabilityProfile(
    family=MowerFamily.GOAT_G1,
    map_dialect=MapDialect.MAP_V2,
    map_uses_v2=True,
    clean_command="clean_V2",
    stop_content_type="",
    clean_always_content=False,
    clean_info_command="getCleanInfo_V2",
    schedule_info_command=None,
    position_fields=("chargePos", "deebotPos", "uwbPos"),
    has_lens_brush=True,
    edge_trim=False,
    experimental=False,
    label="GOAT G1 line (UWB, V2 map)",
)

_O_SERIES_PROFILE = CapabilityProfile(
    family=MowerFamily.GOAT_O_SERIES,
    map_dialect=MapDialect.MAP_RTK,
    map_uses_v2=False,
    clean_command="clean",
    stop_content_type="auto",
    clean_always_content=True,
    clean_info_command="getCleanInfo",
    schedule_info_command="getScheduleTaskInfo",
    position_fields=("chargePos", "deebotPos", "rtkPos"),
    has_lens_brush=False,
    edge_trim=False,
    experimental=True,
    label="GOAT O-series (RTK, getMapTrack dialect, experimental)",
)

_UNKNOWN_PROFILE = CapabilityProfile(
    family=MowerFamily.UNKNOWN,
    map_dialect=MapDialect.MAP_V2,
    map_uses_v2=True,
    clean_command="clean_V2",
    stop_content_type="",
    clean_always_content=False,
    clean_info_command="getCleanInfo_V2",
    schedule_info_command=None,
    position_fields=("chargePos", "deebotPos", "uwbPos"),
    has_lens_brush=True,
    edge_trim=False,
    experimental=False,
    label="Unknown GOAT (assuming G1 / V2 map)",
)

_FAMILY_PROFILES: dict[MowerFamily, CapabilityProfile] = {
    MowerFamily.GOAT_G1: _G1_PROFILE,
    MowerFamily.GOAT_O_SERIES: _O_SERIES_PROFILE,
    MowerFamily.UNKNOWN: _UNKNOWN_PROFILE,
}


def profile_for_family(family: MowerFamily | str) -> CapabilityProfile:
    """Return the capability profile for a coarse family."""
    try:
        family = MowerFamily(family)
    except ValueError:
        family = MowerFamily.UNKNOWN
    return _FAMILY_PROFILES[family]


def profile_for_model(device_name: str | None) -> CapabilityProfile:
    """Return the capability profile for an ECOVACS ``deviceName``."""
    variant = classify_goat_g1_variant(device_name)
    profile = profile_for_family(variant_family(variant))
    if variant in _EDGE_TRIM_VARIANTS:
        profile = replace(profile, edge_trim=True)
    return profile
