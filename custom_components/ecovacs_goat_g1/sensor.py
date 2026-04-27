"""ECOVACS GOAT mower sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONF_DESCRIPTION,
    PERCENTAGE,
    EntityCategory,
    UnitOfArea,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import EcovacsConfigEntry
from .entity import EcovacsMowerEntity
from .mower_models import MowerState


@dataclass(kw_only=True, frozen=True)
class MowerSensorDescription(SensorEntityDescription):
    """Mower sensor description."""

    value_fn: Callable[[MowerState], StateType]
    attr_fn: Callable[[MowerState], dict[str, Any] | None] | None = None


SENSORS: tuple[MowerSensorDescription, ...] = (
    MowerSensorDescription(
        key="battery_level",
        name="Battery level",
        value_fn=lambda state: state.battery,
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MowerSensorDescription(
        key="error",
        name="Error",
        value_fn=lambda state: state.error_code,
        attr_fn=lambda state: {CONF_DESCRIPTION: state.error_description},
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MowerSensorDescription(
        key="network_ip",
        name="IP address",
        value_fn=lambda state: state.network.ip,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MowerSensorDescription(
        key="network_rssi",
        name="Wi-Fi RSSI",
        value_fn=lambda state: state.network.rssi,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MowerSensorDescription(
        key="network_ssid",
        name="Wi-Fi SSID",
        value_fn=lambda state: state.network.ssid,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MowerSensorDescription(
        key="stats_area",
        name="Area mowed",
        value_fn=lambda state: state.stats.area,
        native_unit_of_measurement=UnitOfArea.SQUARE_CENTIMETERS,
        suggested_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        device_class=SensorDeviceClass.AREA,
    ),
    MowerSensorDescription(
        key="stats_job_area",
        name="Mowing area",
        value_fn=lambda state: state.stats.job_area,
        native_unit_of_measurement=UnitOfArea.SQUARE_CENTIMETERS,
        suggested_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        device_class=SensorDeviceClass.AREA,
    ),
    MowerSensorDescription(
        key="stats_progress",
        name="Mowing progress",
        value_fn=lambda state: state.stats.progress,
        native_unit_of_measurement=PERCENTAGE,
    ),
    MowerSensorDescription(
        key="stats_time",
        name="Mowing duration",
        value_fn=lambda state: state.stats.duration,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
    ),
    MowerSensorDescription(
        key="total_stats_area",
        name="Total area mowed",
        value_fn=lambda state: state.stats.total_area,
        native_unit_of_measurement=UnitOfArea.SQUARE_CENTIMETERS,
        suggested_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.AREA,
    ),
    MowerSensorDescription(
        key="total_stats_time",
        name="Total mowing duration",
        value_fn=lambda state: state.stats.total_duration,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.DURATION,
    ),
    MowerSensorDescription(
        key="total_stats_cleanings",
        name="Total mowings",
        value_fn=lambda state: state.stats.total_count,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    MowerSensorDescription(
        key="lifespan_blade",
        name="Blade lifespan",
        value_fn=lambda state: state.lifespans.get("blade"),
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MowerSensorDescription(
        key="lifespan_lens_brush",
        name="Lens brush lifespan",
        value_fn=lambda state: state.lifespans.get("lensBrush"),
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: EcovacsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add mower sensors."""
    async_add_entities(
        MowerSensor(coordinator, description)
        for coordinator in config_entry.runtime_data.coordinators
        for description in SENSORS
    )


class MowerSensor(EcovacsMowerEntity, SensorEntity):
    """Mower sensor."""

    entity_description: MowerSensorDescription

    def __init__(
        self, coordinator, entity_description: MowerSensorDescription
    ) -> None:
        """Initialize sensor."""
        self.entity_description = entity_description
        super().__init__(coordinator, entity_description.key)

    @property
    def native_value(self) -> StateType:
        """Return native value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        if self.entity_description.attr_fn is None:
            return None
        return self.entity_description.attr_fn(self.coordinator.data)
