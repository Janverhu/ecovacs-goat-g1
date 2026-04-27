"""ECOVACS GOAT mower switches."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EcovacsConfigEntry
from .entity import EcovacsMowerEntity
from .mower_models import MowerState


@dataclass(kw_only=True, frozen=True)
class MowerSwitchDescription(SwitchEntityDescription):
    """Mower switch description."""

    value_fn: Callable[[MowerState], bool | None]


SWITCHES: tuple[MowerSwitchDescription, ...] = (
    MowerSwitchDescription(
        key="rain_sensor",
        name="Rain sensor",
        value_fn=lambda state: state.settings.rain_enabled,
        entity_category=EntityCategory.CONFIG,
    ),
    MowerSwitchDescription(
        key="animal_protection",
        name="Animal protection",
        value_fn=lambda state: state.settings.animal_enabled,
        entity_category=EntityCategory.CONFIG,
    ),
    MowerSwitchDescription(
        key="ai_recognition",
        name="AI Recognition",
        value_fn=lambda state: state.settings.ai_recognition,
        entity_category=EntityCategory.CONFIG,
    ),
    MowerSwitchDescription(
        key="border_switch",
        name="Border switch",
        value_fn=lambda state: state.settings.border_switch,
        entity_category=EntityCategory.CONFIG,
    ),
    MowerSwitchDescription(
        key="safer_mode",
        name="Safer Mode",
        value_fn=lambda state: state.settings.safer_mode,
        entity_category=EntityCategory.CONFIG,
    ),
    MowerSwitchDescription(
        key="move_up_warning",
        name="Move up warning",
        value_fn=lambda state: state.settings.move_up_warning,
        entity_category=EntityCategory.CONFIG,
    ),
    MowerSwitchDescription(
        key="cross_map_border_warning",
        name="Cross map border warning",
        value_fn=lambda state: state.settings.cross_map_border_warning,
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: EcovacsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add mower switches."""
    async_add_entities(
        MowerSwitch(coordinator, description)
        for coordinator in config_entry.runtime_data.coordinators
        for description in SWITCHES
    )


class MowerSwitch(EcovacsMowerEntity, SwitchEntity):
    """Mower switch."""

    entity_description: MowerSwitchDescription

    def __init__(
        self, coordinator, entity_description: MowerSwitchDescription
    ) -> None:
        """Initialize switch."""
        self.entity_description = entity_description
        super().__init__(coordinator, entity_description.key)

    @property
    def is_on(self) -> bool | None:
        """Return switch state."""
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on switch."""
        await self.coordinator.set_enabled(self.entity_description.key, True)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off switch."""
        await self.coordinator.set_enabled(self.entity_description.key, False)
