"""Base entities for the ECOVACS GOAT mower driver."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .mower_coordinator import MowerCoordinator


class EcovacsMowerEntity(CoordinatorEntity[MowerCoordinator]):
    """Base coordinator entity for one ECOVACS mower."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MowerCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device.did}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry info."""
        device = self.coordinator.device
        info = DeviceInfo(
            identifiers={(DOMAIN, device.did)},
            manufacturer="Ecovacs",
            name=device.name,
            model=device.model,
            model_id=device.device_class,
            serial_number=device.did,
        )
        if self.coordinator.data.network.mac:
            info["connections"] = {
                (dr.CONNECTION_NETWORK_MAC, self.coordinator.data.network.mac)
            }
        return info

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self.coordinator.data.available

    async def async_update(self) -> None:
        """Refresh stale cached state when Home Assistant explicitly requests it."""
        await self.coordinator.async_refresh_if_stale()
