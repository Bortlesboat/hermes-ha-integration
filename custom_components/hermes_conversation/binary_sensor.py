"""Diagnostic binary sensors for Hermes Agent."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .entity import HermesConversationActivityEntity, HermesDiagnosticsEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Callable[[list[Any]], None],
) -> None:
    """Set up Hermes diagnostic binary sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    conversation_activity = hass.data[DOMAIN][entry.entry_id]["conversation_activity"]
    async_add_entities(
        [
            HermesApiConnectivityBinarySensor(coordinator, entry),
            HermesConversationProcessingBinarySensor(conversation_activity, entry),
        ]
    )


class HermesConversationProcessingBinarySensor(
    HermesConversationActivityEntity, BinarySensorEntity
):
    """Whether any local conversation request is currently active."""

    _attr_icon = "mdi:message-processing"

    def __init__(self, conversation_activity, entry: ConfigEntry) -> None:
        super().__init__(conversation_activity, entry, "processing_conversation")

    @property
    def is_on(self) -> bool:
        return self.conversation_activity.active_request_count > 0


class HermesApiConnectivityBinarySensor(HermesDiagnosticsEntity, BinarySensorEntity):
    """Authenticated API reachability; off is not the same as unavailable."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "api_connectivity")

    @property
    def available(self) -> bool:
        return self.diagnostics is not None

    @property
    def is_on(self) -> bool:
        data = self.diagnostics
        return bool(data and data.connected)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.diagnostics
        if data and data.error_category:
            return {"error_category": data.error_category}
        return None
