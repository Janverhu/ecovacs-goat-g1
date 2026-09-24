"""Tests for which ECOVACS robots stay on the account."""

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

from custom_components.ecovacs_goat_g1.mower_api import merge_bound_devices


def _bound(did: str, *, company: str = "eco-ng", nick: str = "Robot") -> dict[str, str]:
    return {
        "did": did,
        "class": "class-a",
        "resource": "res-a",
        "nick": nick,
        "company": company,
    }


def _global(did: str, *, device_name: str, category: str) -> dict[str, str]:
    return {
        "did": did,
        "class": "class-a",
        "resource": "res-a",
        "company": "eco-ng",
        "deviceName": device_name,
        "product_category": category,
        "nick": "From global",
    }


def test_bound_device_keeps_global_metadata() -> None:
    """A robot in both lists stays, with the product name from the global list."""
    merged = merge_bound_devices(
        [_bound("mower-1", nick="Yard")],
        [_global("mower-1", device_name="GOAT G1-800", category="GOATBOT")],
    )

    assert [device["did"] for device in merged] == ["mower-1"]
    assert merged[0]["deviceName"] == "GOAT G1-800"
    assert merged[0]["product_category"] == "GOATBOT"
    assert merged[0]["nick"] == "Yard"


def test_global_only_device_is_dropped() -> None:
    """A robot that remains only in the global list is no longer bound."""
    merged = merge_bound_devices(
        [_bound("mower-1")],
        [
            _global("mower-1", device_name="GOAT G1-800", category="GOATBOT"),
            _global("vacuum-old", device_name="DEEBOT T9+", category="DEEBOT"),
        ],
    )

    assert [device["did"] for device in merged] == ["mower-1"]


def test_bound_deebot_is_kept() -> None:
    """Product filtering is a later change; a bound vacuum still stays."""
    merged = merge_bound_devices(
        [_bound("mower-1"), _bound("vacuum-1", nick="Hall")],
        [
            _global("mower-1", device_name="GOAT G1-800", category="GOATBOT"),
            _global("vacuum-1", device_name="DEEBOT X2", category="DEEBOT"),
        ],
    )

    assert [device["did"] for device in merged] == ["mower-1", "vacuum-1"]
    assert merged[1]["deviceName"] == "DEEBOT X2"


def test_legacy_company_is_skipped() -> None:
    """Only current eco-ng robots are set up."""
    merged = merge_bound_devices(
        [_bound("legacy-1", company="eco-legacy")],
        [],
    )

    assert merged == []
