"""Read Home Assistant states into the engine's snapshot, once per evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_AREA_M2,
    CONF_BOILER_SWITCH,
    CONF_CLOSING_NUMBER,
    CONF_MODE_SELECT,
    CONF_OPENING_NUMBER,
    CONF_OUTDOOR_SENSOR,
    CONF_POSITION_SENSOR,
    CONF_POWER_LEVEL,
    CONF_SENSOR_SELECT,
    CONF_SMART_SWITCH,
    CONF_THERMOSTAT,
    CONF_VALVE,
    CONF_VALVE_PROFILE,
    CONF_WALL_SENSOR,
)
from ..engine.model import (
    MoesValve,
    RoomSnapshot,
    Snapshot,
    SonoffValve,
    Thermostat,
    ThermostatMode,
    Valve,
    ValveProfile,
)
from ..engine.validity import Flag, Reading

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from homeassistant.core import HomeAssistant, State

MISSING = frozenset({STATE_UNAVAILABLE, STATE_UNKNOWN})
# VTherm offers auto as well; for the engine a room in auto heats like one in heat.
HEATING_MODES = frozenset({"heat", "auto"})


@dataclass(frozen=True, slots=True)
class RoomConfig:
    """One room as configured in its subentry."""

    name: str
    data: Mapping[str, Any]

    @property
    def entities(self) -> tuple[str, ...]:
        """Every entity this room reads."""
        return tuple(value for key, value in self.data.items() if _is_entity_key(key) and value)


def _is_entity_key(key: str) -> bool:
    return key not in {CONF_AREA_M2, CONF_VALVE_PROFILE}


class SnapshotBuilder:
    """Knows which entity is which input; turns the current states into a `Snapshot`."""

    def __init__(
        self, hass: HomeAssistant, plant: Mapping[str, Any], rooms: list[RoomConfig]
    ) -> None:
        """Remember the plant's and the rooms' entities."""
        self._hass = hass
        self._plant = plant
        self._rooms = rooms

    @property
    def watched(self) -> tuple[str, ...]:
        """Every entity whose change should trigger a re-evaluation."""
        plant = (self._plant[CONF_BOILER_SWITCH], self._plant[CONF_POWER_LEVEL])
        return (*plant, *(entity for room in self._rooms for entity in room.entities))

    def build(self, now: datetime) -> Snapshot:
        """Read every input; a configured entity that does not exist makes it incomplete."""
        missing: list[str] = []
        rooms = tuple(self._room(room, missing) for room in self._rooms)
        outdoor = self._reading(self._plant[CONF_OUTDOOR_SENSOR], missing)
        return Snapshot(
            now=now,
            taken_at=now,
            complete=not missing,
            rooms=rooms,
            outdoor=outdoor,
            boiler_on=self._boiler_on(missing),
        )

    def _state(self, entity_id: str, missing: list[str]) -> State | None:
        state = self._hass.states.get(entity_id)
        if state is None:
            missing.append(entity_id)
        return state

    def _reading(self, entity_id: str, missing: list[str]) -> Reading:
        state = self._state(entity_id, missing)
        if state is None:
            return Reading(None, self._epoch())
        return Reading(_number(state.state), state.last_reported)

    def _boiler_on(self, missing: list[str]) -> bool | None:
        switch = self._state(self._plant[CONF_BOILER_SWITCH], missing)
        power = self._state(self._plant[CONF_POWER_LEVEL], missing)
        if switch is None or power is None or switch.state in MISSING or power.state in MISSING:
            return None
        return switch.state == STATE_ON

    def _room(self, room: RoomConfig, missing: list[str]) -> RoomSnapshot:
        data = room.data
        thermostat_state = self._state(data[CONF_THERMOSTAT], missing)
        return RoomSnapshot(
            name=room.name,
            area_m2=float(data[CONF_AREA_M2]),
            thermostat=self._thermostat(thermostat_state),
            wall=self._reading(data[CONF_WALL_SENSOR], missing),
            valve=self._valve(data, missing),
        )

    def _thermostat(self, state: State | None) -> Thermostat:
        if state is None or state.state in MISSING:
            return Thermostat(mode=None, target=Reading(None, self._epoch()))
        mode = (
            ThermostatMode.HEAT
            if state.state in HEATING_MODES
            else ThermostatMode.OFF
            if state.state == "off"
            else None
        )
        return Thermostat(
            mode=mode,
            target=Reading(_number(state.attributes.get("temperature")), state.last_reported),
            window_open=_window_open(state.attributes),
        )

    def _valve(self, data: Mapping[str, Any], missing: list[str]) -> Valve:
        valve = self._state(data[CONF_VALVE], missing)
        heating = _heating(valve, self._epoch())
        if ValveProfile(data[CONF_VALVE_PROFILE]) is ValveProfile.MOES:
            return MoesValve(
                heating=heating,
                position=self._reading(data[CONF_POSITION_SENSOR], missing),
                preset=None if valve is None else _text(valve.attributes.get("preset_mode")),
                mode=self._text(data[CONF_MODE_SELECT], missing),
            )
        return SonoffValve(
            heating=heating,
            system_mode=None if valve is None else _text(valve.state),
            opening_degree_pct=self._reading(data[CONF_OPENING_NUMBER], missing).value,
            closing_degree_pct=self._reading(data[CONF_CLOSING_NUMBER], missing).value,
            smart_temperature_control=self._text(data[CONF_SMART_SWITCH], missing),
            temperature_sensor_select=self._text(data[CONF_SENSOR_SELECT], missing),
        )

    def _text(self, entity_id: str, missing: list[str]) -> str | None:
        state = self._state(entity_id, missing)
        return None if state is None else _text(state.state)

    @staticmethod
    def _epoch() -> datetime:
        """Return a timestamp for readings that never arrived; always stale."""
        return dt_util.utc_from_timestamp(0)


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value not in MISSING:
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _text(value: object) -> str | None:
    if not isinstance(value, str) or value in MISSING:
        return None
    return value


def _heating(state: State | None, epoch: datetime) -> Flag:
    if state is None or state.state in MISSING:
        return Flag(None, epoch)
    action = state.attributes.get("hvac_action")
    if not isinstance(action, str):
        return Flag(None, state.last_reported)
    return Flag(action == "heating", state.last_reported)


def _window_open(attributes: Mapping[str, Any]) -> bool:
    """VTherm reports the window under `window_manager`; older versions at the top level."""
    manager = attributes.get("window_manager")
    if isinstance(manager, dict) and manager.get("window_state") == STATE_ON:
        return True
    return attributes.get("window_state") == STATE_ON
