"""ECOVACS GOAT G1 button entities."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EcovacsConfigEntry
from .entity import EcovacsMowerEntity

BUTTONS: tuple[ButtonEntityDescription, ...] = (
    ButtonEntityDescription(
        key="refresh_state",
        name="Refresh state",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ButtonEntityDescription(
        key="end_mowing",
        name="End mowing",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: EcovacsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add mower button entities."""
    async_add_entities(
        MowerButton(coordinator, description)
        for coordinator in config_entry.runtime_data.coordinators
        for description in BUTTONS
    )


class MowerButton(EcovacsMowerEntity, ButtonEntity):
    """Mower button entity."""

    entity_description: ButtonEntityDescription

    def __init__(
        self, coordinator, entity_description: ButtonEntityDescription
    ) -> None:
        """Initialize button."""
        self.entity_description = entity_description
        super().__init__(coordinator, entity_description.key)

    async def async_press(self) -> None:
        """Handle button press."""
        match self.entity_description.key:
            case "refresh_state":
                await self.coordinator.async_refresh_if_stale()
            case "end_mowing":
                await self.coordinator.end_mowing()
