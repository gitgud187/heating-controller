"""Binary sensors per room: demand, served, blocked, valve open, valve healthy, overheating."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory

from .const import SUBENTRY_ROOM
from .entity import RoomEntity

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import HeatingControllerConfigEntry
    from .adapter.controller import HeatingController
    from .engine.demand import RoomAssessment

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class RoomBinaryDescription(BinarySensorEntityDescription):
    """A yes/no fact about a room; `controller_fn` sees the controller as well."""

    value_fn: Callable[[RoomAssessment, HeatingController], bool]


ROOM_BINARY_SENSORS: tuple[RoomBinaryDescription, ...] = (
    RoomBinaryDescription(
        key="demand",
        translation_key="demand",
        device_class=BinarySensorDeviceClass.HEAT,
        value_fn=lambda room, _c: room.needs_heat,
    ),
    RoomBinaryDescription(
        key="served", translation_key="served", value_fn=lambda room, _c: room.served
    ),
    RoomBinaryDescription(
        key="blocked",
        translation_key="blocked",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda room, _c: room.blocked,
    ),
    RoomBinaryDescription(
        key="valve_open",
        translation_key="valve_open",
        device_class=BinarySensorDeviceClass.OPENING,
        value_fn=lambda room, _c: room.valve.open,
    ),
    RoomBinaryDescription(
        key="valve_problem",
        translation_key="valve_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda room, _c: room.valve.data_missing or not room.valve.healthy,
    ),
    RoomBinaryDescription(
        key="overheating",
        translation_key="overheating",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda room, c: room.name in c.overheating,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - platform signature
    entry: HeatingControllerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Each room's binary sensors on its subentry."""
    controller = entry.runtime_data
    for room in entry.get_subentries_of_type(SUBENTRY_ROOM):
        async_add_entities(
            (
                RoomBinarySensor(controller, entry, room, description)
                for description in ROOM_BINARY_SENSORS
            ),
            config_subentry_id=room.subentry_id,
        )


class RoomBinarySensor(RoomEntity, BinarySensorEntity):
    """A yes/no fact about one room in the last evaluation."""

    entity_description: RoomBinaryDescription

    def __init__(
        self,
        controller: HeatingController,
        entry: HeatingControllerConfigEntry,
        room: Any,
        description: RoomBinaryDescription,
    ) -> None:
        """Bind the description."""
        super().__init__(controller, entry, room, description.key)
        self.entity_description = description
        self._attr_translation_key = description.translation_key

    @property
    @override
    def is_on(self) -> bool | None:
        """Read it from the room's assessment."""
        assessment = self.assessment
        if assessment is None:
            return None
        return self.entity_description.value_fn(assessment, self.controller)
