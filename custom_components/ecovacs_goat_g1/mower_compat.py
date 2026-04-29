"""Per-device protocol compatibility for eco-ng mowers (GOAT and variants).

The integration targets GOAT G1-style commands. Other eco-ng devices may omit
optional getInfo keys, reject grouped getInfo batches, lack map V2 control APIs,
or omit UWB in getPos.

This module centralises adaptive behaviour without adding new product features:
the same HA entities and services apply; only transport details vary.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import logging
from typing import TYPE_CHECKING, Any

from .mower_api import EcovacsApiError
from .mower_messages import apply_response
from .mower_models import MowerState

if TYPE_CHECKING:
    from .mower_api import EcovacsMowerApi
    from .mower_models import MowerDevice

_LOGGER = logging.getLogger(__name__)

# When a mower rejects getCleanInfo_V2, try legacy clean info (same apply_command_data path).
GETINFO_CLEAN_FALLBACKS: dict[str, str] = {"getCleanInfo_V2": "getCleanInfo"}
GETINFO_UNSUPPORTED_FAILURE_THRESHOLD = 3


@dataclass(frozen=True)
class ProtocolProfile:
    """Learned command preferences for one device."""

    map_api_uses_v2: bool = True
    get_pos_fields: tuple[str, ...] = ("chargePos", "deebotPos", "uwbPos")
    unsupported_getinfo: frozenset[str] = frozenset()
    getinfo_failures: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot for diagnostics."""
        return {
            "map_api_uses_v2": self.map_api_uses_v2,
            "get_pos_fields": list(self.get_pos_fields),
            "unsupported_getinfo": sorted(self.unsupported_getinfo),
            "getinfo_failures": dict(sorted(self.getinfo_failures.items())),
        }


async def apply_resilient_getinfo_group(
    api: EcovacsMowerApi,
    device: MowerDevice,
    state: MowerState,
    commands: tuple[str, ...],
    profile: ProtocolProfile,
) -> tuple[MowerState, ProtocolProfile]:
    """Apply one getInfo group, falling back to per-command reads if the batch fails."""
    active = _active_getinfo_commands(commands, profile.unsupported_getinfo)
    if not active:
        return state, profile

    try:
        response = await api.control(device, "getInfo", active)
        failures = dict(profile.getinfo_failures)
        for cmd in active:
            failures.pop(cmd, None)
        return apply_response(state, "getInfo", response), replace(
            profile, getinfo_failures=failures
        )
    except EcovacsApiError as err:
        _LOGGER.debug(
            "ECOVACS grouped getInfo failed (%s); retrying %s per-command",
            err,
            active,
        )

    unsupported = set(profile.unsupported_getinfo)
    failures = dict(profile.getinfo_failures)
    new_state = state
    for cmd in active:
        if cmd in unsupported:
            continue
        try:
            response = await api.control(device, "getInfo", [cmd])
            new_state = apply_response(new_state, "getInfo", response)
            failures.pop(cmd, None)
        except EcovacsApiError:
            _record_getinfo_failure(cmd, failures, unsupported)
            alt = GETINFO_CLEAN_FALLBACKS.get(cmd)
            if alt and alt not in unsupported:
                try:
                    response = await api.control(device, "getInfo", [alt])
                    new_state = apply_response(new_state, "getInfo", response)
                    failures.pop(alt, None)
                except EcovacsApiError:
                    _record_getinfo_failure(alt, failures, unsupported)
                    _LOGGER.debug(
                        "ECOVACS getInfo %s and fallback %s are not supported",
                        cmd,
                        alt,
                    )
            else:
                _LOGGER.debug("ECOVACS getInfo %s is not supported on this device", cmd)

    return new_state, replace(
        profile,
        unsupported_getinfo=frozenset(unsupported),
        getinfo_failures=failures,
    )


def _record_getinfo_failure(
    command: str, failures: dict[str, int], unsupported: set[str]
) -> None:
    """Record one getInfo failure and only cache unsupported after repeats."""
    failures[command] = failures.get(command, 0) + 1
    if failures[command] >= GETINFO_UNSUPPORTED_FAILURE_THRESHOLD:
        unsupported.add(command)
        _LOGGER.debug("ECOVACS getInfo %s is not supported on this device", command)
        return
    _LOGGER.debug(
        "ECOVACS getInfo %s failed (%s/%s); will retry on a future refresh",
        command,
        failures[command],
        GETINFO_UNSUPPORTED_FAILURE_THRESHOLD,
    )


def _active_getinfo_commands(
    commands: tuple[str, ...], unsupported: frozenset[str]
) -> list[str]:
    """Return getInfo commands to request, substituting known read fallbacks."""
    active: list[str] = []
    for command in commands:
        if command not in unsupported:
            active.append(command)
            continue
        fallback = GETINFO_CLEAN_FALLBACKS.get(command)
        if fallback and fallback not in unsupported:
            active.append(fallback)
    return active


async def refresh_live_position(
    api: EcovacsMowerApi,
    device: MowerDevice,
    state: MowerState,
    profile: ProtocolProfile,
) -> tuple[MowerState, ProtocolProfile]:
    """Refresh getPos, dropping optional fields the firmware rejects."""
    fields = list(profile.get_pos_fields)
    try:
        response = await api.control(device, "getPos", fields)
        return apply_response(state, "getPos", response), profile
    except EcovacsApiError as err:
        if "uwbPos" in fields:
            _LOGGER.debug(
                "ECOVACS getPos with uwbPos failed (%s); retrying without UWB fields",
                err,
            )
            reduced: tuple[str, ...] = tuple(
                f for f in profile.get_pos_fields if f != "uwbPos"
            )
            if reduced != profile.get_pos_fields:
                profile = replace(profile, get_pos_fields=reduced)
                return await refresh_live_position(api, device, state, profile)
        raise
