"""ECOVACS GOAT mower time entities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EcovacsConfigEntry
from .entity import EcovacsMowerEntity


@dataclass(kw_only=True, frozen=True)
class MowerTimeDescription(TimeEntityDescription):
    """Mower time description."""

    cache_key: str


TIMES: tuple[MowerTimeDescription, ...] = (
    MowerTimeDescription(
        key="animal_protection_start",
        name="Animal protection start",
        cache_key="animal_start",
        entity_category=EntityCategory.CONFIG,
    ),
    MowerTimeDescription(
        key="animal_protection_end",
        name="Animal protection end",
        cache_key="animal_end",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: EcovacsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add mower time entities."""
    async_add_entities(
        MowerTime(coordinator, description)
        for coordinator in config_entry.runtime_data.coordinators
        for description in TIMES
    )


class MowerTime(EcovacsMowerEntity, TimeEntity):
    """Mower time entity."""

    entity_description: MowerTimeDescription

    def __init__(self, coordinator, entity_description: MowerTimeDescription) -> None:
        """Initialize time entity."""
        self.entity_description = entity_description
        super().__init__(coordinator, entity_description.key)

    @property
    def native_value(self) -> time | None:
        """Return time value."""
        value = getattr(self.coordinator.data.settings, self.entity_description.cache_key)
        return _parse_time(value)

    async def async_set_value(self, value: time) -> None:
        """Set time value."""
        await self.coordinator.set_animal_time(
            self.entity_description.cache_key,
            f"{value.hour:02d}:{value.minute:02d}",
        )


def _parse_time(value: str | None) -> time | None:
    """Parse HH:MM into a time value."""
    if not value:
        return None
    hour, minute = value.split(":", 1)
    return time(hour=int(hour), minute=int(minute))
