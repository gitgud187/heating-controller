from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util
from pytest_homeassistant.components.diagnostics import get_diagnostics_for_config_entry

from custom_components.heating_controller.engine.state import EngineState

from .conftest import Calls, set_plant, setup

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant.typing import ClientSessionGenerator


def value(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


async def test_controller_and_room_entities(hass: HomeAssistant, calls: Calls) -> None:
    set_plant(hass, bedroom_c=18.0, living_c=21.0)
    entry = await setup(hass)
    assert value(hass, "sensor.heating_controller_load") == "48.0"
    assert float(value(hass, "sensor.heating_controller_output")) > 30
    assert value(hass, "sensor.heating_controller_output_base") == "56.7"
    assert value(hass, "sensor.heating_controller_starts_in_the_last_hour") == "1"
    assert value(hass, "sensor.heating_controller_run_time") == "0.0"
    assert value(hass, "sensor.heating_controller_locked_out_until") == "unknown"
    assert value(hass, "sensor.bedroom_status") == "served"
    assert value(hass, "sensor.bedroom_load_share") == "48.0"
    assert value(hass, "sensor.bedroom_below_target") == "3.0"
    assert value(hass, "sensor.living_status") == "satisfied"
    assert value(hass, "binary_sensor.bedroom_demand") == "on"
    assert value(hass, "binary_sensor.bedroom_served") == "on"
    assert value(hass, "binary_sensor.bedroom_blocked") == "off"
    assert value(hass, "binary_sensor.bedroom_valve_open") == "on"
    assert value(hass, "binary_sensor.living_overheating") == "off"
    ctrl = entry.runtime_data
    hass.states.async_set("switch.boiler", "on")  # the mocked service did not switch it
    ctrl.state = EngineState(
        on_since=dt_util.utcnow() - timedelta(minutes=30), starts=(dt_util.utcnow(),)
    )
    await ctrl.async_evaluate()
    assert float(value(hass, "sensor.heating_controller_run_time")) >= 30


async def test_entities_before_the_first_evaluation(hass: HomeAssistant, calls: Calls) -> None:
    set_plant(hass)
    entry = await setup(hass)
    entry.runtime_data.last = None
    from custom_components.heating_controller.sensor import (  # noqa: PLC0415 - unit check
        CONTROLLER_SENSORS,
        boiler_state,
    )

    assert boiler_state(entry.runtime_data) is None
    assert all(d.value_fn(entry.runtime_data) is None for d in CONTROLLER_SENSORS)
    room = hass.data["entity_components"]["sensor"].get_entity("sensor.bedroom_status")
    assert room.native_value is None
    assert not room.available
    assert room.extra_state_attributes is None
    binary = hass.data["entity_components"]["binary_sensor"].get_entity(
        "binary_sensor.bedroom_demand"
    )
    assert binary.is_on is None


async def test_diagnostics(
    hass: HomeAssistant, calls: Calls, hass_client: ClientSessionGenerator
) -> None:
    set_plant(hass)
    entry = await setup(hass)
    diagnostics: dict[str, Any] = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diagnostics["data"]["notify_service"] == "**REDACTED**"
    assert set(diagnostics["rooms"]) == {"Bedroom", "Living"}
    assert diagnostics["state"]["version"] == 3
    assert diagnostics["history"][0]["reasons"][0]["code"] == "start_load"
    assert diagnostics["history"][0]["rooms"]["Bedroom"] == "served"


async def test_a_card_can_read_the_room_and_the_thresholds(
    hass: HomeAssistant, calls: Calls
) -> None:
    """The load sensor carries its thresholds, the status sensor the room's readings."""
    set_plant(hass, bedroom_c=18.0, living_c=21.0)
    entry = await setup(hass)

    load = hass.states.get("sensor.heating_controller_load")
    assert load is not None
    assert load.attributes["start_load_m2k"] == 4.0
    assert load.attributes["stop_load_m2k"] == 1.5
    output = hass.states.get("sensor.heating_controller_output")
    assert output is not None
    assert "start_load_m2k" not in output.attributes

    bedroom = hass.states.get("sensor.bedroom_status")
    assert bedroom is not None
    assert bedroom.attributes["area_m2"] == 16.0
    assert bedroom.attributes["current_temperature"] == 18.0
    assert bedroom.attributes["target_temperature"] == 21.0
    assert bedroom.attributes["valve_position"] == 40.0

    living = hass.states.get("sensor.living_status")
    assert living is not None
    assert living.attributes["valve_position"] is None

    # Sensors that stop reporting leave the attribute empty instead of guessing.
    hass.states.async_set("sensor.bedroom_temperature", "unavailable")
    hass.states.async_set("sensor.living_temperature", "21.0")
    hass.states.async_remove("sensor.living_temperature")
    hass.states.async_set("climate.bedroom", "unavailable")
    await entry.runtime_data.async_evaluate()
    await hass.async_block_till_done()

    bedroom = hass.states.get("sensor.bedroom_status")
    assert bedroom is not None
    assert bedroom.attributes["current_temperature"] is None
    assert bedroom.attributes["target_temperature"] is None
    living = hass.states.get("sensor.living_status")
    assert living is not None
    assert living.attributes["current_temperature"] is None
