"""Tests for eco-ng protocol compatibility helpers."""

from pathlib import Path
import sys
import types
from unittest.mock import AsyncMock

import pytest

PACKAGE_PATH = Path(__file__).parents[2] / "custom_components" / "ecovacs_goat_g1"

custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(PACKAGE_PATH.parent)]
sys.modules.setdefault("custom_components", custom_components)

ecovacs_goat_g1 = types.ModuleType("custom_components.ecovacs_goat_g1")
ecovacs_goat_g1.__path__ = [str(PACKAGE_PATH)]
sys.modules.setdefault("custom_components.ecovacs_goat_g1", ecovacs_goat_g1)

from custom_components.ecovacs_goat_g1.mower_api import EcovacsApiError
from custom_components.ecovacs_goat_g1.mower_compat import (
    GETINFO_UNSUPPORTED_FAILURE_THRESHOLD,
    ProtocolProfile,
    apply_resilient_getinfo_group,
    refresh_live_position,
)
from custom_components.ecovacs_goat_g1.mower_models import MowerState


@pytest.mark.asyncio
async def test_resilient_getinfo_keeps_single_failures_retryable() -> None:
    """A single per-command failure should not permanently disable the readback."""
    api = AsyncMock()
    device = object()
    api.control.side_effect = [
        EcovacsApiError("batch failed"),
        {"body": {"data": {"getBattery": {"data": {"value": 88, "isLow": 0}}}}},
        EcovacsApiError("unsupported"),
    ]

    state, profile = await apply_resilient_getinfo_group(
        api,
        device,
        MowerState(),
        ("getBattery", "getOta"),
        ProtocolProfile(),
    )

    assert state.battery == 88
    assert "getOta" not in profile.unsupported_getinfo
    assert profile.getinfo_failures["getOta"] == 1
    assert api.control.call_count == 3


@pytest.mark.asyncio
async def test_resilient_getinfo_marks_repeated_failures_unsupported() -> None:
    """Repeated per-command failures are eventually cached as unsupported."""
    api = AsyncMock()
    device = object()
    profile = ProtocolProfile()

    for _ in range(GETINFO_UNSUPPORTED_FAILURE_THRESHOLD):
        api.control.side_effect = [
            EcovacsApiError("batch failed"),
            EcovacsApiError("unsupported"),
        ]
        _, profile = await apply_resilient_getinfo_group(
            api,
            device,
            MowerState(),
            ("getOta",),
            profile,
        )

    assert "getOta" in profile.unsupported_getinfo


@pytest.mark.asyncio
async def test_resilient_getinfo_uses_clean_info_fallback_after_v2_disabled() -> None:
    """If getCleanInfo_V2 is cached unsupported, request getCleanInfo instead."""
    api = AsyncMock()
    device = object()
    profile = ProtocolProfile(unsupported_getinfo=frozenset({"getCleanInfo_V2"}))
    api.control.return_value = {
        "body": {
            "data": {
                "getCleanInfo": {"data": {"trigger": "none", "state": "idle"}}
            }
        }
    }

    state, _ = await apply_resilient_getinfo_group(
        api,
        device,
        MowerState(),
        ("getCleanInfo_V2",),
        profile,
    )

    assert state.raw["getCleanInfo"]["state"] == "idle"
    assert api.control.call_args_list[0][0][2] == ["getCleanInfo"]


@pytest.mark.asyncio
async def test_refresh_live_position_drops_uwb_on_error() -> None:
    """If getPos with uwbPos fails, retry without UWB and persist the narrower field set."""
    api = AsyncMock()
    device = object()
    api.control.side_effect = [
        EcovacsApiError("bad uwb field"),
        {"body": {"data": {"deebotPos": {"x": 1, "y": 2}}}},
    ]
    profile = ProtocolProfile()

    state, profile_out = await refresh_live_position(
        api, device, MowerState(), profile
    )

    assert state.map.current_position is not None
    assert state.map.current_position.x == 1
    assert "uwbPos" not in profile_out.get_pos_fields
    assert api.control.call_args_list[0][0][2] == ["chargePos", "deebotPos", "uwbPos"]
    assert api.control.call_args_list[1][0][2] == ["chargePos", "deebotPos"]
