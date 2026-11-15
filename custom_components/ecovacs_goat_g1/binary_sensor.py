"""ECOVACS GOAT mower binary sensors."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EcovacsConfigEntry
from .entity import EcovacsMowerEntity

CHARGING = BinarySensorEntityDescription(
    key="charging",
    name="Charging",
    device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: EcovacsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add mower binary sensors."""
    async_add_entities(
        ChargingBinarySensor(coordinator)
        for coordinator in config_entry.runtime_data.coordinators
    )


class ChargingBinarySensor(EcovacsMowerEntity, BinarySensorEntity):
    """Whether the mower is charging on its dock."""

    entity_description = CHARGING

    def __init__(self, coordinator) -> None:
        """Initialize the charging sensor."""
        super().__init__(coordinator, CHARGING.key)

    @property
    def is_on(self) -> bool | None:
        """Return whether getChargeState/onChargeState report charging."""
        return self.coordinator.data.charging
