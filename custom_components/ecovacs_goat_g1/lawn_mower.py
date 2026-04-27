"""ECOVACS GOAT lawn mower entity."""

from __future__ import annotations

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityEntityDescription,
    LawnMowerEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EcovacsConfigEntry
from .entity import EcovacsMowerEntity
from .mower_models import MowerActivity

ACTIVITY_MAP = {
    MowerActivity.IDLE: None,
    MowerActivity.MOWING: LawnMowerActivity.MOWING,
    MowerActivity.PAUSED: LawnMowerActivity.PAUSED,
    MowerActivity.RETURNING: LawnMowerActivity.RETURNING,
    MowerActivity.DOCKED: LawnMowerActivity.DOCKED,
    MowerActivity.ERROR: LawnMowerActivity.ERROR,
    MowerActivity.UNKNOWN: None,
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: EcovacsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the ECOVACS mowers."""
    async_add_entities(
        EcovacsMower(coordinator)
        for coordinator in config_entry.runtime_data.coordinators
    )


class EcovacsMower(EcovacsMowerEntity, LawnMowerEntity):
    """ECOVACS GOAT mower."""

    _attr_supported_features = (
        LawnMowerEntityFeature.DOCK
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.START_MOWING
    )
    entity_description = LawnMowerEntityEntityDescription(key="mower", name=None)

    def __init__(self, coordinator) -> None:
        """Initialize the mower entity."""
        super().__init__(coordinator, "mower")

    @property
    def activity(self) -> LawnMowerActivity | None:
        """Return mower activity."""
        return ACTIVITY_MAP[self.coordinator.data.activity]

    async def async_start_mowing(self) -> None:
        """Start or resume mowing."""
        await self.coordinator.start_mowing()

    async def async_pause(self) -> None:
        """Pause mowing."""
        await self.coordinator.pause()

    async def async_dock(self) -> None:
        """Return to dock/charge."""
        await self.coordinator.dock()
