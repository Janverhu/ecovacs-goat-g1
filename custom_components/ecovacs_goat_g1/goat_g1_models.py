"""GOAT G1 family model hints (G1, G1-2000, G1-800 / G-800).

The official app uses one H5 protocol bundle for multiple retail SKUs. The cloud
device list exposes a human-readable ``deviceName`` string; we map that to a
compact variant id for diagnostics and future capability branching.

Ecovacs marketing aligns the **G1** line (e.g. G1-800, G1-2000) under the same
app feature set as the base GOAT G1; this module only **classifies** names — it
does not change protocol behaviour until we have model-specific captures.
"""

from __future__ import annotations

# Values exposed on sensors / diagnostics (stable API for automations).
VARIANT_G1 = "g1"
VARIANT_G1_2000 = "g1_2000"
VARIANT_G1_800 = "g1_800"
VARIANT_G1_1600 = "g1_1600"
VARIANT_UNKNOWN = "unknown"

VARIANT_LABELS: dict[str, str] = {
    VARIANT_G1: "GOAT G1",
    VARIANT_G1_2000: "GOAT G1-2000",
    VARIANT_G1_800: "GOAT G1-800",
    VARIANT_G1_1600: "GOAT G1-1600",
    VARIANT_UNKNOWN: "Unknown",
}


def classify_goat_g1_variant(device_name: str | None) -> str:
    """Map ECOVACS ``deviceName`` (nick/name) to a G1-line variant id.

    Matching is conservative: longer / more specific product tokens win over
    the generic ``G1`` substring (e.g. ``GOAT G1-2000`` → ``g1_2000``).
    """
    if not device_name:
        return VARIANT_UNKNOWN

    compact = "".join(device_name.split()).upper()
    upper = device_name.upper()

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


def variant_label(variant_id: str) -> str:
    """Return a short human label for a variant id."""
    return VARIANT_LABELS.get(variant_id, VARIANT_LABELS[VARIANT_UNKNOWN])
