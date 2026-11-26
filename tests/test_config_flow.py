from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry

from custom_components.heating_controller.const import (
    CONF_BOILER_SWITCH,
    CONF_HEARTBEAT,
    CONF_OUTDOOR_SENSOR,
    CONF_POWER_LEVEL,
    DOMAIN,
    SUBENTRY_ROOM,
)

from .adapter.conftest import BEDROOM, Calls, make_entry, set_plant, setup

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

USER_INPUT = {
    CONF_BOILER_SWITCH: "switch.boiler",
    CONF_POWER_LEVEL: "number.boiler_power",
    CONF_HEARTBEAT: "button.boiler_heartbeat",
    CONF_OUTDOOR_SENSOR: "sensor.outdoor",
}
COMMON = ("thermostat", "wall_sensor", "area_m2", "valve")


async def test_user_step_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == USER_INPUT


async def test_only_one_entry(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, data=USER_INPUT).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_add_a_moes_room(hass: HomeAssistant) -> None:
    entry = make_entry(subentries_data=())
    entry.add_to_hass(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ROOM), context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    first = {"name": " Bedroom "} | {key: BEDROOM[key] for key in COMMON}
    first["position_sensor"] = BEDROOM["position_sensor"]
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], first)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (room,) = entry.get_subentries_of_type(SUBENTRY_ROOM)
    assert room.title == "Bedroom"
    assert dict(room.data) == BEDROOM


async def test_reconfigure_a_room_without_valve_entities(hass: HomeAssistant) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    bedroom = next(r for r in entry.get_subentries_of_type(SUBENTRY_ROOM) if r.title == "Bedroom")
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ROOM),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": bedroom.subentry_id},
    )
    assert result["step_id"] == "reconfigure"
    plain = {key: BEDROOM[key] for key in ("thermostat", "wall_sensor", "area_m2")}
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], plain)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert dict(entry.subentries[bedroom.subentry_id].data) == plain


async def test_version_1_rooms_are_migrated(hass: HomeAssistant, calls: Calls) -> None:
    old = BEDROOM | {"valve_profile": "moes", "mode_select": "select.bedroom_trv_mode"}
    entry = make_entry(
        version=1,
        subentries_data=(
            {"data": old, "subentry_type": SUBENTRY_ROOM, "title": "Bedroom", "unique_id": None},
        ),
    )
    set_plant(hass)
    await setup(hass, entry)
    assert entry.version == 2
    (room,) = entry.get_subentries_of_type(SUBENTRY_ROOM)
    assert dict(room.data) == BEDROOM


async def test_newer_entries_are_refused(hass: HomeAssistant) -> None:
    entry = make_entry(version=3)
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)


async def test_options_flow_stores_numbers(hass: HomeAssistant, calls: Calls) -> None:
    set_plant(hass)
    entry = await setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"start_load_m2k": 5.0, "max_run_h": 5.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["start_load_m2k"] == 5.0
    await hass.async_block_till_done()
    assert entry.runtime_data.config.boiler.start_load_m2k == 5.0
    assert await hass.config_entries.async_unload(entry.entry_id)
