"""GOAT mower model hints (G1 line and O-series).

The official app uses one H5/CodePush bundle for every GOAT SKU, but the bundle
contains two distinct map/control dialects:

* The **G1 line** (G1, G1-800 / G-800, G1-2000, G1-1600) uses the UWB + ``*_V2``
  map dialect (``getMapInfo_V2`` / ``getMapTrace_V2``) that this integration was
  originally built and validated against.
* The newer **O-series** (O800 RTK, O1200, O1200 LiDAR Pro, ...) uses the
  ``clean`` command, ``getCleanInfo``, RTK positions (``rtkPos``), and the
  ``getMapState`` / ``getMapTrack`` / ``getMI`` / ``getAreaSet`` map dialect
  instead (confirmed against a GOAT O800 RTK capture).

The cloud device list exposes a human-readable ``deviceName`` string; this module
maps that to a compact variant id (for diagnostics) and to a coarse *family*
(used by :mod:`.mower_profiles` to pick the protocol dialect).

This module only **classifies** names. It does not itself change protocol
behaviour; see :mod:`.mower_profiles`.
"""

from __future__ import annotations

# Values exposed on sensors / diagnostics (stable API for automations).
VARIANT_G1 = "g1"
VARIANT_G1_2000 = "g1_2000"
VARIANT_G1_800 = "g1_800"
VARIANT_G1_1600 = "g1_1600"
VARIANT_O800_RTK = "o800_rtk"
VARIANT_O1200 = "o1200"
VARIANT_O1200_LIDAR_PRO = "o1200_lidar_pro"
VARIANT_O_SERIES = "o_series"
VARIANT_UNKNOWN = "unknown"

# Coarse families used to pick a protocol dialect (see mower_profiles).
FAMILY_G1 = "goat_g1"
FAMILY_O_SERIES = "goat_o_series"
FAMILY_UNKNOWN = "unknown"

VARIANT_LABELS: dict[str, str] = {
    VARIANT_G1: "GOAT G1",
    VARIANT_G1_2000: "GOAT G1-2000",
    VARIANT_G1_800: "GOAT G1-800",
    VARIANT_G1_1600: "GOAT G1-1600",
    VARIANT_O800_RTK: "GOAT O800 RTK",
    VARIANT_O1200: "GOAT O1200",
    VARIANT_O1200_LIDAR_PRO: "GOAT O1200 LiDAR Pro",
    VARIANT_O_SERIES: "GOAT O-series",
    VARIANT_UNKNOWN: "Unknown",
}

# Each variant id maps to a coarse protocol family.
VARIANT_FAMILIES: dict[str, str] = {
    VARIANT_G1: FAMILY_G1,
    VARIANT_G1_2000: FAMILY_G1,
    VARIANT_G1_800: FAMILY_G1,
    VARIANT_G1_1600: FAMILY_G1,
    VARIANT_O800_RTK: FAMILY_O_SERIES,
    VARIANT_O1200: FAMILY_O_SERIES,
    VARIANT_O1200_LIDAR_PRO: FAMILY_O_SERIES,
    VARIANT_O_SERIES: FAMILY_O_SERIES,
    VARIANT_UNKNOWN: FAMILY_UNKNOWN,
}


def classify_goat_g1_variant(device_name: str | None) -> str:
    """Map ECOVACS ``deviceName`` (nick/name) to a GOAT variant id.

    Matching is conservative: longer / more specific product tokens win over the
    generic family substring (e.g. ``GOAT G1-2000`` -> ``g1_2000``,
    ``GOAT O1200 LiDAR Pro`` -> ``o1200_lidar_pro``). The historical function
    name is kept so existing imports and automations keep working even though it
    now also recognises the O-series.
    """
    if not device_name:
        return VARIANT_UNKNOWN

    compact = "".join(device_name.split()).upper()
    upper = device_name.upper()

    # O-series (LiDAR / RTK map dialect). Order matters: most specific first.
    if (
        "O1200" in compact
        and ("LIDARPRO" in compact or ("LIDAR" in upper and "PRO" in upper))
    ):
        return VARIANT_O1200_LIDAR_PRO
    if "O800" in compact:
        return VARIANT_O800_RTK
    if "O1200" in compact:
        return VARIANT_O1200
    if _is_generic_o_series(upper, compact):
        return VARIANT_O_SERIES

    # G1 line (UWB / V2 map dialect).
    if "G1-2000" in upper or "G1_2000" in upper or "G12000" in compact:
        return VARIANT_G1_2000
    if (
        "G1-800" in upper
        or "G1_800" in upper
        or "G-800" in upper
        or "G1800" in compact
    ):
        return VARIANT_G1_800
    if "G1-1600" in upper or "G1_1600" in upper or "G11600" in compact:
        return VARIANT_G1_1600
    if "GOAT" in upper and "G1" in upper:
        return VARIANT_G1
    if "G1" in upper:
        return VARIANT_G1
    return VARIANT_UNKNOWN


def _is_generic_o_series(upper: str, compact: str) -> bool:
    """Return whether a name looks like an (unlisted) GOAT O-series SKU."""
    if "GOAT" not in upper:
        return False
    # Match a GOAT model token shaped like the O-series (e.g. "O500", "O2950").
    for index, char in enumerate(compact):
        if char != "O":
            continue
        rest = compact[index + 1 : index + 5]
        digits = ""
        for digit in rest:
            if digit.isdigit():
                digits += digit
            else:
                break
        if len(digits) >= 3:
            return True
    return False


def variant_label(variant_id: str) -> str:
    """Return a short human label for a variant id."""
    return VARIANT_LABELS.get(variant_id, VARIANT_LABELS[VARIANT_UNKNOWN])


def variant_family(variant_id: str) -> str:
    """Return the coarse protocol family for a variant id."""
    return VARIANT_FAMILIES.get(variant_id, FAMILY_UNKNOWN)


def classify_goat_family(device_name: str | None) -> str:
    """Map ECOVACS ``deviceName`` directly to a coarse protocol family."""
    return variant_family(classify_goat_g1_variant(device_name))
