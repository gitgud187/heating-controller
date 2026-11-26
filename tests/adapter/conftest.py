"""A small fake plant: one Moes room and one Sonoff room, boiler entities, mocked services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

from custom_components.heating_controller.const import (
    CONF_AREA_M2,
    CONF_BOILER_SWITCH,
    CONF_CLOSING_NUMBER,
    CONF_HEARTBEAT,
    CONF_MODE_SELECT,
    CONF_NOTIFY_SERVICE,
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
    DOMAIN,
    SUBENTRY_ROOM,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

PLANT = {
    CONF_BOILER_SWITCH: "switch.boiler",
    CONF_POWER_LEVEL: "number.boiler_power",
    CONF_HEARTBEAT: "button.boiler_heartbeat",
    CONF_OUTDOOR_SENSOR: "sensor.outdoor",
    CONF_NOTIFY_SERVICE: "notify.phone",
}
BEDROOM = {
    CONF_THERMOSTAT: "climate.bedroom",
    CONF_WALL_SENSOR: "sensor.bedroom_temperature",
    CONF_AREA_M2: 16.0,
    CONF_VALVE_PROFILE: "moes",
    CONF_VALVE: "climate.bedroom_trv",
    CONF_POSITION_SENSOR: "sensor.bedroom_trv_position",
    CONF_MODE_SELECT: "select.bedroom_trv_mode",
}
LIVING = {
    CONF_THERMOSTAT: "climate.living",
    CONF_WALL_SENSOR: "sensor.living_temperature",
    CONF_AREA_M2: 18.0,
    CONF_VALVE_PROFILE: "sonoff",
    CONF_VALVE: "climate.living_trv",
    CONF_OPENING_NUMBER: "number.living_trv_opening",
    CONF_CLOSING_NUMBER: "number.living_trv_closing",
    CONF_SMART_SWITCH: "switch.living_trv_smart",
    CONF_SENSOR_SELECT: "select.living_trv_sensor",
}


def make_entry(**changes: Any) -> MockConfigEntry:
    """The controller entry with both rooms as subentries."""
    rooms = (
        {"data": BEDROOM, "subentry_type": SUBENTRY_ROOM, "title": "Bedroom", "unique_id": None},
        {"data": LIVING, "subentry_type": SUBENTRY_ROOM, "title": "Living", "unique_id": None},
    )
    arguments: dict[str, Any] = {
        "domain": DOMAIN,
        "title": "Heating Controller",
        "data": PLANT,
        "subentries_data": rooms,
    } | changes
    return MockConfigEntry(**arguments)


def set_plant(  # noqa: PLR0913 - every knob of the fake house
    hass: HomeAssistant,
    *,
    bedroom_c: float = 18.0,
    living_c: float = 21.0,
    target_c: float = 21.0,
    boiler: str = "off",
    power: str = "30",
    bedroom_heating: bool = True,
    living_heating: bool = False,
) -> None:
    """Write every state the controller reads."""
    hass.states.async_set("switch.boiler", boiler)
    hass.states.async_set("number.boiler_power", power)
    hass.states.async_set("button.boiler_heartbeat", "unknown")
    hass.states.async_set("sensor.outdoor", "0.0")
    for room, wall in (("bedroom", bedroom_c), ("living", living_c)):
        hass.states.async_set(
            f"climate.{room}",
            "heat",
            {"temperature": target_c, "window_manager": {"window_state": "off"}},
        )
        hass.states.async_set(f"sensor.{room}_temperature", str(wall))
    hass.states.async_set(
        "climate.bedroom_trv",
        "heat",
        {"hvac_action": "heating" if bedroom_heating else "idle", "preset_mode": "manual"},
    )
    hass.states.async_set("sensor.bedroom_trv_position", "40" if bedroom_heating else "0")
    hass.states.async_set("select.bedroom_trv_mode", "comfort")
    hass.states.async_set(
        "climate.living_trv", "heat", {"hvac_action": "heating" if living_heating else "idle"}
    )
    hass.states.async_set("number.living_trv_opening", "100")
    hass.states.async_set("number.living_trv_closing", "100")
    hass.states.async_set("switch.living_trv_smart", "off")
    hass.states.async_set("select.living_trv_sensor", "local_temperature")


@pytest.fixture(autouse=True)
def no_problem_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report problems at once; the delay itself has its own test."""
    monkeypatch.setattr(
        "custom_components.heating_controller.adapter.controller.PROBLEM_DELAY", timedelta(0)
    )


@dataclass
class Calls:
    """Every mocked service the controller uses."""

    turn_on: list[ServiceCall]
    turn_off: list[ServiceCall]
    set_value: list[ServiceCall]
    press: list[ServiceCall]
    notify: list[ServiceCall]


@pytest.fixture
async def calls(hass: HomeAssistant) -> Calls:
    """
    Mock the services and return their call logs.

    The entity components are set up first, so their real services do not replace the mocks
    when the integration's platforms load them.
    """
    for domain in ("switch", "number", "button"):
        assert await async_setup_component(hass, domain, {})
    return Calls(
        turn_on=async_mock_service(hass, "switch", "turn_on"),
        turn_off=async_mock_service(hass, "switch", "turn_off"),
        set_value=async_mock_service(hass, "number", "set_value"),
        press=async_mock_service(hass, "button", "press"),
        notify=async_mock_service(hass, "notify", "phone"),
    )


async def setup(hass: HomeAssistant, entry: MockConfigEntry | None = None) -> MockConfigEntry:
    """Add and set up the entry; return it."""
    entry = entry or make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
