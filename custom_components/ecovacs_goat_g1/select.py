"""ECOVACS GOAT mower select entities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EcovacsConfigEntry
from .entity import EcovacsMowerEntity
from .mower_messages import MOWING_EFFICIENCY_OPTIONS, OBSTACLE_AVOIDANCE_OPTIONS
from .mower_models import MowerState

MOWING_EFFICIENCY_LABELS = {
    "quick": "Quick",
    "delicate": "Delicate",
}
OBSTACLE_AVOIDANCE_LABELS = {
    "short_grass": "Short grass",
    "general": "General",
    "bumpy_tall_grass": "Bumpy ground with tall grass",
}


@dataclass(kw_only=True, frozen=True)
class MowerSelectDescription(SelectEntityDescription):
    """Mower select description."""

    option_fn: Callable[[MowerState], str | None]
    options_map: dict[str, str]


SELECTS: tuple[MowerSelectDescription, ...] = (
    MowerSelectDescription(
        key="mowing_efficiency",
        name="Mowing efficiency",
        option_fn=lambda state: _label(
            MOWING_EFFICIENCY_LABELS, state.settings.mowing_efficiency
        ),
        options_map={
            MOWING_EFFICIENCY_LABELS[option]: option
            for option in MOWING_EFFICIENCY_OPTIONS
        },
        entity_category=EntityCategory.CONFIG,
    ),
    MowerSelectDescription(
        key="obstacle_avoidance",
        name="Obstacle avoidance",
        option_fn=lambda state: _label(
            OBSTACLE_AVOIDANCE_LABELS, state.settings.obstacle_avoidance
        ),
        options_map={
            OBSTACLE_AVOIDANCE_LABELS[option]: option
            for option in OBSTACLE_AVOIDANCE_OPTIONS
        },
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: EcovacsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add mower select entities."""
    async_add_entities(
        MowerSelect(coordinator, description)
        for coordinator in config_entry.runtime_data.coordinators
        for description in SELECTS
    )


class MowerSelect(EcovacsMowerEntity, SelectEntity):
    """Mower select entity."""

    entity_description: MowerSelectDescription

    def __init__(
        self, coordinator, entity_description: MowerSelectDescription
    ) -> None:
        """Initialize entity."""
        self.entity_description = entity_description
        super().__init__(coordinator, entity_description.key)
        self._attr_options = list(entity_description.options_map)

    @property
    def current_option(self) -> str | None:
        """Return selected option."""
        return self.entity_description.option_fn(self.coordinator.data)

    async def async_select_option(self, option: str) -> None:
        """Select an option."""
        value = self.entity_description.options_map[option]
        if self.entity_description.key == "mowing_efficiency":
            await self.coordinator.set_mowing_efficiency(value)
        elif self.entity_description.key == "obstacle_avoidance":
            await self.coordinator.set_obstacle_avoidance(value)


def _label(labels: dict[str, str], option: str | None) -> str | None:
    """Return Home Assistant label for an internal option."""
    if option is None:
        return None
    return labels.get(option)
