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
    VARIANT_G1,
    VARIANT_G1_2000,
    VARIANT_G1_800,
    VARIANT_UNKNOWN,
    classify_goat_g1_variant,
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


def test_variant_label() -> None:
    assert "G1-2000" in variant_label(VARIANT_G1_2000)
