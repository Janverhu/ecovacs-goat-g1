"""ECOVACS GOAT G1 button entities."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.components.persistent_notification import async_create, async_dismiss
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
        name="Stop mowing",
    ),
    ButtonEntityDescription(
        key="start_debug_capture",
        name="Start debug capture",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ButtonEntityDescription(
        key="stop_debug_capture",
        name="Stop debug capture",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ButtonEntityDescription(
        key="export_debug_capture",
        name="Export debug capture",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ButtonEntityDescription(
        key="clear_debug_capture",
        name="Clear debug captures",
        entity_category=EntityCategory.DIAGNOSTIC,
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
                await self.coordinator.async_refresh_state()
            case "end_mowing":
                await self.coordinator.end_mowing()
            case "start_debug_capture":
                self.coordinator.debug_capture.start(reason="Started from Home Assistant UI")
                self.coordinator.async_set_updated_data(self.coordinator.data)
            case "stop_debug_capture":
                self.coordinator.debug_capture.stop()
                self.coordinator.async_set_updated_data(self.coordinator.data)
            case "export_debug_capture":
                export = self.coordinator.debug_capture.export_zip()
                base_url = (
                    self.coordinator.hass.config.external_url
                    or self.coordinator.hass.config.internal_url
                    or ""
                )
                download_url = export["url"]
                full_url = (
                    f"{base_url.rstrip('/')}{download_url}" if base_url else download_url
                )
                async_create(
                    self.coordinator.hass,
                    (
                        "ECOVACS debug capture export is ready.\n\n"
                        f"[Download capture]({full_url})\n\n"
                        f"URL: {full_url}"
                    ),
                    title="ECOVACS debug capture",
                    notification_id="ecovacs_goat_debug_capture_export",
                )
                self.coordinator.async_set_updated_data(self.coordinator.data)
            case "clear_debug_capture":
                self.coordinator.debug_capture.clear()
                async_dismiss(
                    self.coordinator.hass,
                    notification_id="ecovacs_goat_debug_capture_export",
                )
                self.coordinator.async_set_updated_data(self.coordinator.data)
