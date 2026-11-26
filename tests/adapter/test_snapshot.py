from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

from custom_components.heating_controller.adapter.snapshot import RoomConfig, SnapshotBuilder
from custom_components.heating_controller.engine.model import (
    ThermostatMode,
)

from .conftest import BEDROOM, LIVING, PLANT, set_plant

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def builder(hass: HomeAssistant) -> SnapshotBuilder:
    return SnapshotBuilder(
        hass, PLANT, [RoomConfig("Bedroom", BEDROOM), RoomConfig("Living", LIVING)]
    )


async def test_maps_the_valve_sources(hass: HomeAssistant) -> None:
    set_plant(hass, bedroom_c=18.5, boiler="on")
    snapshot = builder(hass).build(dt_util.utcnow())
    assert snapshot.complete
    assert snapshot.boiler_on is True
    assert snapshot.outdoor.value == 0.0
    bedroom, living = snapshot.rooms
    assert bedroom.wall.value == 18.5
    assert bedroom.thermostat.mode is ThermostatMode.HEAT
    assert bedroom.thermostat.target.value == 21.0
    assert not bedroom.thermostat.window_open
    assert bedroom.valve.heating is not None
    assert bedroom.valve.heating.value is True
    assert bedroom.valve.position is not None
    assert bedroom.valve.position.value == 40.0
    assert living.valve.heating is not None
    assert living.valve.heating.value is False
    assert living.valve.position is None


async def test_unavailable_and_missing_entities(hass: HomeAssistant) -> None:
    set_plant(hass)
    hass.states.async_set("climate.bedroom_trv", "unavailable")
    hass.states.async_set("sensor.living_temperature", "unknown")
    hass.states.async_set("climate.living", "unavailable")
    hass.states.async_remove("sensor.bedroom_trv_position")
    hass.states.async_set("number.boiler_power", "unavailable")
    snapshots = builder(hass)
    snapshot = snapshots.build(dt_util.utcnow())
    assert snapshot.complete  # only a missing boiler entity makes it incomplete
    assert snapshots.missing == ("sensor.bedroom_trv_position",)
    assert snapshot.boiler_on is None
    bedroom, living = snapshot.rooms
    assert bedroom.valve.heating is not None
    assert bedroom.valve.heating.value is None
    assert bedroom.valve.position is not None
    assert bedroom.valve.position.value is None
    assert living.wall.value is None
    assert living.thermostat.mode is None


async def test_modes_window_and_odd_values(hass: HomeAssistant) -> None:
    set_plant(hass)
    hass.states.async_set("climate.bedroom", "auto", {"temperature": "20.5", "window_state": "on"})
    hass.states.async_set("climate.living", "off", {"temperature": None})
    hass.states.async_set("climate.living_trv", "heat", {"hvac_action": None})
    hass.states.async_set("sensor.outdoor", "n/a")
    snapshot = builder(hass).build(dt_util.utcnow())
    bedroom, living = snapshot.rooms
    assert bedroom.thermostat.mode is ThermostatMode.HEAT
    assert bedroom.thermostat.target.value == 20.5
    assert bedroom.thermostat.window_open
    assert living.thermostat.mode is ThermostatMode.OFF
    assert living.thermostat.target.value is None
    assert living.valve.heating is not None
    assert living.valve.heating.value is None
    assert snapshot.outdoor.value is None
    hass.states.async_set("climate.bedroom", "fan_only", {"temperature": True})
    bedroom = builder(hass).build(dt_util.utcnow()).rooms[0]
    assert bedroom.thermostat.mode is None
    assert bedroom.thermostat.target.value is None


async def test_watched_entities(hass: HomeAssistant) -> None:
    watched = builder(hass).watched
    assert "switch.boiler" in watched
    assert "sensor.bedroom_temperature" in watched
    assert "climate.living_trv" in watched
    assert "sensor.outdoor" not in watched


async def test_a_missing_boiler_entity_makes_it_incomplete(hass: HomeAssistant) -> None:
    set_plant(hass)
    hass.states.async_remove("switch.boiler")
    snapshots = builder(hass)
    snapshot = snapshots.build(dt_util.utcnow())
    assert not snapshot.complete
    assert snapshot.boiler_on is None
    assert snapshots.missing == ("switch.boiler",)


async def test_without_valve_entities_the_thermostat_stands_in(hass: HomeAssistant) -> None:
    set_plant(hass)
    hass.states.async_set("climate.living", "heat", {"temperature": 21.0, "hvac_action": "heating"})
    room = {k: v for k, v in LIVING.items() if k != "valve"}
    snapshot = SnapshotBuilder(hass, PLANT, [RoomConfig("Living", room)]).build(dt_util.utcnow())
    (living,) = snapshot.rooms
    assert living.valve.heating is not None
    assert living.valve.heating.value is True
    position_only = {**room, "position_sensor": "sensor.bedroom_trv_position"}
    snapshot = SnapshotBuilder(hass, PLANT, [RoomConfig("Living", position_only)]).build(
        dt_util.utcnow()
    )
    assert snapshot.rooms[0].valve.heating is None
    assert snapshot.rooms[0].valve.position is not None
