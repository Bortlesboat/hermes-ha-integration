"""Shared device metadata for Hermes conversation and diagnostic entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .activity import ConversationActivityTracker
from .const import DOMAIN
from .coordinator import HermesDiagnosticsCoordinator, HermesDiagnosticsData


def hermes_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the per-entry device shared by conversation and diagnostics."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=getattr(entry, "title", None) or "Hermes Agent",
        manufacturer="Nous Research",
        model="Hermes Agent",
        entry_type=DeviceEntryType.SERVICE,
    )


class HermesConversationActivityEntity(Entity):
    """Push-only observer of HA requests on the per-entry device."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        conversation_activity: ConversationActivityTracker,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        super().__init__()
        self.conversation_activity = conversation_activity
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = hermes_device_info(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.conversation_activity.subscribe(self.async_write_ha_state)
        )


class HermesDiagnosticsEntity(CoordinatorEntity[HermesDiagnosticsCoordinator]):
    """Base diagnostic entity attached to the per-entry Hermes device."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HermesDiagnosticsCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = hermes_device_info(entry)

    @property
    def diagnostics(self) -> HermesDiagnosticsData | None:
        """Return the current whitelist snapshot, if any."""
        data = self.coordinator.data
        if isinstance(data, HermesDiagnosticsData):
            return data
        return None
