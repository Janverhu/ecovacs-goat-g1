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

The O-series map geometry (``getMapTrack`` / ``getMI`` / ``getAreaSet`` blobs)
is **not decoded**: the only available O-series capture was taken while docked,
where ``getMapTrack`` returns ``fail``, so there is no validated geometry sample.
The O-series live map is therefore driven by the (shared, decodable) position
stream; the map id is still learned so a future active-mowing capture can finish
the work. O-series support remains **experimental / best-effort**.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .goat_g1_models import (
    FAMILY_G1,
    FAMILY_O_SERIES,
    FAMILY_UNKNOWN,
    classify_goat_family,
)


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
    # Fields requested via getPos (UWB vs RTK reference points).
    position_fields: tuple[str, ...]
    experimental: bool
    label: str

    def clean_body(self, act: str) -> dict[str, Any]:
        """Return the ``clean`` / ``clean_V2`` body for a mowing action.

        ``act`` is one of ``start`` / ``resume`` / ``pause`` / ``stop``.
        """
        if self.clean_always_content:
            return {"act": act, "content": {"type": "auto"}}
        if act == "start":
            return {"act": "start", "content": {"type": "auto"}}
        if act == "stop":
            return {"act": "stop", "content": {"type": self.stop_content_type}}
        return {"act": act}

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot for diagnostics."""
        return {
            "family": str(self.family),
            "map_dialect": str(self.map_dialect),
            "map_uses_v2": self.map_uses_v2,
            "clean_command": self.clean_command,
            "clean_info_command": self.clean_info_command,
            "position_fields": list(self.position_fields),
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
    position_fields=("chargePos", "deebotPos", "uwbPos"),
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
    position_fields=("deebotPos", "chargePos"),
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
    position_fields=("chargePos", "deebotPos", "uwbPos"),
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
    return profile_for_family(classify_goat_family(device_name))
