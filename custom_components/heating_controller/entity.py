"""Base entities: one controller device, one device per room subentry."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry, ConfigSubentry

    from .adapter.controller import HeatingController
    from .engine.demand import RoomAssessment


class ControllerEntity(Entity):
    """An entity of the controller device; updates after every evaluation."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, controller: HeatingController, entry: ConfigEntry, key: str) -> None:
        """Name it by translation key; unique per entry and key."""
        self.controller = controller
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            entry_type=DeviceEntryType.SERVICE,
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the controller."""
        self.async_on_remove(self.controller.async_add_listener(self.async_write_ha_state))


class RoomEntity(ControllerEntity):
    """An entity of one room; its device belongs to the room subentry."""

    def __init__(
        self,
        controller: HeatingController,
        entry: ConfigEntry,
        room: ConfigSubentry,
        key: str,
    ) -> None:
        """Name it by translation key; unique per subentry and key."""
        super().__init__(controller, entry, key)
        self.room_name = room.title
        self._attr_unique_id = f"{room.subentry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, room.subentry_id)},
            name=room.title,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def assessment(self) -> RoomAssessment | None:
        """Return the room as the last evaluation saw it."""
        last = self.controller.last
        if last is None:
            return None
        return next((room for room in last.rooms if room.name == self.room_name), None)

    @property
    @override
    def available(self) -> bool:
        """Unavailable until the room has been evaluated."""
        return self.assessment is not None
