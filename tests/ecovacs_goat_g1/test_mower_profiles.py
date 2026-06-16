"""Tests for GOAT capability profiles (per-model protocol selection)."""

from pathlib import Path
import sys
import types

PACKAGE_PATH = Path(__file__).parents[2] / "custom_components" / "ecovacs_goat_g1"

custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(PACKAGE_PATH.parent)]
sys.modules.setdefault("custom_components", custom_components)

ecovacs_goat_g1 = types.ModuleType("custom_components.ecovacs_goat_g1")
ecovacs_goat_g1.__path__ = [str(PACKAGE_PATH)]
sys.modules.setdefault("custom_components.ecovacs_goat_g1", ecovacs_goat_g1)

from custom_components.ecovacs_goat_g1.mower_profiles import (
    CapabilityProfile,
    MapDialect,
    MowerFamily,
    profile_for_family,
    profile_for_model,
)


def test_g1_profile_uses_v2_map_and_clean_v2() -> None:
    profile = profile_for_model("ECOVACS GOAT G1-800")
    assert profile.family is MowerFamily.GOAT_G1
    assert profile.map_dialect is MapDialect.MAP_V2
    assert profile.map_uses_v2 is True
    assert profile.experimental is False
    assert profile.clean_command == "clean_V2"
    assert profile.clean_info_command == "getCleanInfo_V2"
    assert profile.position_fields == ("chargePos", "deebotPos", "uwbPos")


def test_o_series_profile_matches_o800_rtk_capture() -> None:
    """O-series profile reflects the decrypted GOAT O800 RTK capture."""
    profile = profile_for_model("ECOVACS GOAT O1200")
    assert profile.family is MowerFamily.GOAT_O_SERIES
    assert profile.map_dialect is MapDialect.MAP_RTK
    assert profile.map_uses_v2 is False
    assert profile.experimental is True
    assert profile.clean_command == "clean"
    assert profile.clean_info_command == "getCleanInfo"
    assert profile.position_fields == ("deebotPos", "chargePos")


def test_g1_clean_body_matches_legacy_behaviour() -> None:
    profile = profile_for_model("ECOVACS GOAT G1-800")
    assert profile.clean_body("start") == {"act": "start", "content": {"type": "auto"}}
    assert profile.clean_body("resume") == {"act": "resume"}
    assert profile.clean_body("pause") == {"act": "pause"}
    assert profile.clean_body("stop") == {"act": "stop", "content": {"type": ""}}


def test_o_series_clean_body_always_carries_auto_content() -> None:
    """The O800 RTK capture sends content:{type:auto} on every clean act."""
    profile = profile_for_model("ECOVACS GOAT O800 RTK")
    for act in ("start", "resume", "pause", "stop"):
        assert profile.clean_body(act) == {"act": act, "content": {"type": "auto"}}


def test_unknown_model_defaults_to_validated_v2_behaviour() -> None:
    """Unknown models must not regress: they keep the validated G1/V2 behaviour."""
    profile = profile_for_model("Something Else")
    assert profile.map_uses_v2 is True
    assert profile.map_dialect is MapDialect.MAP_V2
    assert profile.clean_command == "clean_V2"


def test_profile_for_family_accepts_raw_string() -> None:
    profile = profile_for_family("goat_o_series")
    assert profile.family is MowerFamily.GOAT_O_SERIES
    assert profile_for_family("nonsense").family is MowerFamily.UNKNOWN


def test_profile_as_dict_is_serialisable() -> None:
    profile: CapabilityProfile = profile_for_model("ECOVACS GOAT O800 RTK")
    snapshot = profile.as_dict()
    assert snapshot["family"] == "goat_o_series"
    assert snapshot["map_dialect"] == "map_rtk"
    assert snapshot["clean_command"] == "clean"
    assert snapshot["experimental"] is True
