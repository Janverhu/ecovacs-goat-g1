"""Tests for GOAT G1 retail SKU classification."""

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

from custom_components.ecovacs_goat_g1.goat_g1_models import (
    FAMILY_G1,
    FAMILY_O_SERIES,
    FAMILY_UNKNOWN,
    VARIANT_G1,
    VARIANT_G1_2000,
    VARIANT_G1_800,
    VARIANT_O800_RTK,
    VARIANT_O1200,
    VARIANT_O1200_LIDAR_PRO,
    VARIANT_O_SERIES,
    VARIANT_UNKNOWN,
    classify_goat_family,
    classify_goat_g1_variant,
    variant_family,
    variant_label,
)


def test_classify_g1_2000() -> None:
    assert classify_goat_g1_variant("ECOVACS GOAT G1-2000") == VARIANT_G1_2000
    assert classify_goat_g1_variant("GOATG1-2000") == VARIANT_G1_2000


def test_classify_g1_800_aliases() -> None:
    assert classify_goat_g1_variant("GOAT G1-800") == VARIANT_G1_800
    assert classify_goat_g1_variant("GOAT G-800") == VARIANT_G1_800


def test_classify_base_g1() -> None:
    assert classify_goat_g1_variant("ECOVACS GOAT G1") == VARIANT_G1
    assert classify_goat_g1_variant("GOAT GX") == VARIANT_UNKNOWN


def test_classify_o1200_lidar_pro_wins_over_o1200() -> None:
    assert (
        classify_goat_g1_variant("ECOVACS GOAT O1200 LiDAR Pro")
        == VARIANT_O1200_LIDAR_PRO
    )
    assert classify_goat_g1_variant("GOAT O1200") == VARIANT_O1200


def test_classify_o800_rtk() -> None:
    assert classify_goat_g1_variant("ECOVACS GOAT O800 RTK") == VARIANT_O800_RTK


def test_classify_generic_o_series() -> None:
    assert classify_goat_g1_variant("ECOVACS GOAT O2950") == VARIANT_O_SERIES


def test_variant_label() -> None:
    assert "G1-2000" in variant_label(VARIANT_G1_2000)
    assert "O1200" in variant_label(VARIANT_O1200_LIDAR_PRO)


def test_variant_family_mapping() -> None:
    assert variant_family(VARIANT_G1_800) == FAMILY_G1
    assert variant_family(VARIANT_O1200) == FAMILY_O_SERIES
    assert variant_family(VARIANT_UNKNOWN) == FAMILY_UNKNOWN


def test_classify_goat_family() -> None:
    assert classify_goat_family("ECOVACS GOAT G1-800") == FAMILY_G1
    assert classify_goat_family("ECOVACS GOAT O1200") == FAMILY_O_SERIES
    assert classify_goat_family(None) == FAMILY_UNKNOWN
