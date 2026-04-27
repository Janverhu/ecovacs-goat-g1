"""ECOVACS GOAT mower number entities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import DEGREE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EcovacsConfigEntry
from .entity import EcovacsMowerEntity
from .mower_models import MowerState


@dataclass(kw_only=True, frozen=True)
class MowerNumberDescription(NumberEntityDescription):
    """Mower number description."""

    value_fn: Callable[[MowerState], float | None]


NUMBERS: tuple[MowerNumberDescription, ...] = (
    MowerNumberDescription(
        key="rain_delay",
        name="Rain delay",
        value_fn=lambda state: state.settings.rain_delay,
        native_min_value=0,
        native_max_value=1440,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    MowerNumberDescription(
        key="cut_direction",
        name="Cut direction",
        value_fn=lambda state: state.settings.cut_direction,
        native_min_value=0,
        native_max_value=180,
        native_step=1,
        native_unit_of_measurement=DEGREE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: EcovacsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add mower number entities."""
    async_add_entities(
        MowerNumber(coordinator, description)
        for coordinator in config_entry.runtime_data.coordinators
        for description in NUMBERS
    )


class MowerNumber(EcovacsMowerEntity, NumberEntity):
    """Mower number entity."""

    entity_description: MowerNumberDescription

    def __init__(
        self, coordinator, entity_description: MowerNumberDescription
    ) -> None:
        """Initialize entity."""
        self.entity_description = entity_description
        super().__init__(coordinator, entity_description.key)

    @property
    def native_value(self) -> float | None:
        """Return number value."""
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_set_native_value(self, value: float) -> None:
        """Set new value."""
        if self.entity_description.key == "rain_delay":
            await self.coordinator.set_rain_delay(int(value))
        elif self.entity_description.key == "cut_direction":
            await self.coordinator.set_cut_direction(int(value))
