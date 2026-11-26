"""Sensors: what the controller decided and why, and each room's share."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AREA_M2,
    CONF_POSITION_SENSOR,
    CONF_THERMOSTAT,
    CONF_WALL_SENSOR,
    SUBENTRY_ROOM,
)
from .engine.demand import RoomStatus
from .entity import ControllerEntity, RoomEntity

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import HeatingControllerConfigEntry
    from .adapter.controller import HeatingController
    from .engine.demand import RoomAssessment

PARALLEL_UPDATES = 0
LOAD_UNIT = "m²·K"

BOILER_STATES = ["off", "on", "locked_out", "paused", "unavailable"]


def _entity_id(data: Mapping[str, Any], key: str) -> str | None:
    """Return a configured entity id, or None when the key is missing or not an id."""
    value = data.get(key)
    return value if isinstance(value, str) else None


def _number_state(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """Return a numeric state, or None when the entity is missing or not a number."""
    state = hass.states.get(entity_id) if entity_id else None
    if state is None:
        return None
    try:
        return float(state.state)
    except TypeError, ValueError:
        return None


def _number_attribute(hass: HomeAssistant, entity_id: str | None, attribute: str) -> float | None:
    """Return a numeric attribute, or None when it is missing or not a number."""
    state = hass.states.get(entity_id) if entity_id else None
    if state is None:
        return None
    value = state.attributes.get(attribute)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def boiler_state(controller: HeatingController) -> str | None:
    """Off, on, locked out, paused or unavailable."""
    last = controller.last
    if last is None:
        return None
    if last.command is None:
        return "unavailable"
    if last.command.heat:
        return "on"
    if controller.controls.paused:
        return "paused"
    lockout = last.state.lockout_until
    if lockout is not None and lockout > dt_util.utcnow():
        return "locked_out"
    return "off"


def _output(controller: HeatingController) -> int | None:
    """Return what the boiler really gets: the request while it runs, 0 while it is off."""
    last = controller.last
    if last is None or last.command is None:
        return None
    return last.command.modulation_pct if last.command.heat else 0


def _run_minutes(controller: HeatingController) -> float | None:
    if controller.last is None:
        return None
    on_since = controller.last.state.on_since
    if on_since is None:
        return 0.0
    return round((dt_util.utcnow() - on_since).total_seconds() / 60, 1)


@dataclass(frozen=True, kw_only=True)
class ControllerSensorDescription(SensorEntityDescription):
    """A controller sensor and how to read it."""

    value_fn: Callable[[HeatingController], float | int | str | datetime | None]


def _modulation(attr: str) -> Callable[[HeatingController], float | None]:
    def read(controller: HeatingController) -> float | None:
        last = controller.last
        return None if last is None else round(float(getattr(last.modulation, attr)), 1)

    return read


CONTROLLER_SENSORS: tuple[ControllerSensorDescription, ...] = (
    ControllerSensorDescription(
        key="boiler",
        translation_key="boiler",
        device_class=SensorDeviceClass.ENUM,
        options=BOILER_STATES,
        value_fn=boiler_state,
    ),
    ControllerSensorDescription(
        key="load",
        translation_key="load",
        native_unit_of_measurement=LOAD_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda c: None if c.last is None else round(c.last.load_m2k, 2),
    ),
    ControllerSensorDescription(
        key="modulation",
        translation_key="modulation",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_output,
    ),
    ControllerSensorDescription(
        key="base",
        translation_key="base",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_modulation("base_pct"),
    ),
    ControllerSensorDescription(
        key="proportional",
        translation_key="proportional",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_modulation("p_pct"),
    ),
    ControllerSensorDescription(
        key="integral",
        translation_key="integral",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_modulation("integral_pct"),
    ),
    ControllerSensorDescription(
        key="starts_last_hour",
        translation_key="starts_last_hour",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: None if c.last is None else c.last.starts_last_hour,
    ),
    ControllerSensorDescription(
        key="run_time",
        translation_key="run_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=_run_minutes,
    ),
    ControllerSensorDescription(
        key="lockout_until",
        translation_key="lockout_until",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: None if c.last is None else c.last.state.lockout_until,
    ),
)


@dataclass(frozen=True, kw_only=True)
class RoomSensorDescription(SensorEntityDescription):
    """A room sensor and how to read it from the room's assessment."""

    value_fn: Callable[[RoomAssessment], float | str | None]


ROOM_SENSORS: tuple[RoomSensorDescription, ...] = (
    RoomSensorDescription(
        key="status",
        translation_key="room_status",
        device_class=SensorDeviceClass.ENUM,
        options=[status.value for status in RoomStatus],
        value_fn=lambda room: room.status.value,
    ),
    RoomSensorDescription(
        key="load",
        translation_key="room_load",
        native_unit_of_measurement=LOAD_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda room: round(room.load_m2k, 2),
    ),
    RoomSensorDescription(
        key="error",
        translation_key="room_error",
        native_unit_of_measurement="K",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda room: None if room.error_k is None else round(room.error_k, 2),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - platform signature
    entry: HeatingControllerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the controller sensors, then each room's sensors on its subentry."""
    controller = entry.runtime_data
    async_add_entities(
        ControllerSensor(controller, entry, description) for description in CONTROLLER_SENSORS
    )
    for room in entry.get_subentries_of_type(SUBENTRY_ROOM):
        async_add_entities(
            (RoomSensor(controller, entry, room, description) for description in ROOM_SENSORS),
            config_subentry_id=room.subentry_id,
        )


class ControllerSensor(ControllerEntity, SensorEntity):
    """A value of the last evaluation."""

    entity_description: ControllerSensorDescription

    def __init__(
        self,
        controller: HeatingController,
        entry: HeatingControllerConfigEntry,
        description: ControllerSensorDescription,
    ) -> None:
        """Bind the description."""
        super().__init__(controller, entry, description.key)
        self.entity_description = description
        self._attr_translation_key = description.translation_key

    @property
    @override
    def native_value(self) -> float | int | str | datetime | None:
        """Read it from the controller."""
        return self.entity_description.value_fn(self.controller)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The boiler sensor carries the reason, the load sensor its thresholds."""
        last = self.controller.last
        if last is None:
            return None
        key = self.entity_description.key
        if key == "boiler":
            return {"reason": last.reason.code.value, "message": last.reason.message}
        if key == "load":
            boiler = self.controller.config.boiler
            return {
                "start_load_m2k": boiler.start_load_m2k,
                "stop_load_m2k": boiler.stop_load_m2k,
            }
        return None


class RoomSensor(RoomEntity, SensorEntity):
    """A value of one room in the last evaluation."""

    entity_description: RoomSensorDescription

    def __init__(
        self,
        controller: HeatingController,
        entry: HeatingControllerConfigEntry,
        room: ConfigSubentry,
        description: RoomSensorDescription,
    ) -> None:
        """Bind the description and keep the room's own configuration."""
        super().__init__(controller, entry, room, description.key)
        self.entity_description = description
        self._attr_translation_key = description.translation_key
        self._room = room.data

    @property
    @override
    def native_value(self) -> float | str | None:
        """Read it from the room's assessment."""
        assessment = self.assessment
        return None if assessment is None else self.entity_description.value_fn(assessment)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The status sensor carries the room's readings, for a dashboard card."""
        if self.assessment is None or self.entity_description.key != "status":
            return None
        return {
            "area_m2": self._room.get(CONF_AREA_M2),
            "current_temperature": _number_state(
                self.hass, _entity_id(self._room, CONF_WALL_SENSOR)
            ),
            "target_temperature": _number_attribute(
                self.hass, _entity_id(self._room, CONF_THERMOSTAT), "temperature"
            ),
            "valve_position": _number_state(
                self.hass, _entity_id(self._room, CONF_POSITION_SENSOR)
            ),
        }
