"""Heating Controller: owns a boiler, reads the rooms, never commands them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform

from .adapter.controller import HeatingController
from .adapter.options import engine_config
from .adapter.snapshot import RoomConfig, SnapshotBuilder
from .const import SUBENTRY_ROOM

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS = (Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR, Platform.SWITCH)

type HeatingControllerConfigEntry = ConfigEntry[HeatingController]


def room_configs(entry: ConfigEntry) -> list[RoomConfig]:
    """Return the rooms configured as subentries, in a stable order."""
    rooms = entry.get_subentries_of_type(SUBENTRY_ROOM)
    return [RoomConfig(name=room.title, data=room.data) for room in rooms]


async def async_setup_entry(hass: HomeAssistant, entry: HeatingControllerConfigEntry) -> bool:
    """Start the controller and its entities."""
    builder = SnapshotBuilder(hass, entry.data, room_configs(entry))
    controller = HeatingController(
        hass,
        entry_id=entry.entry_id,
        plant=entry.data,
        builder=builder,
        config=engine_config(entry.options),
    )
    entry.runtime_data = controller
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await controller.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HeatingControllerConfigEntry) -> bool:
    """Stop the controller; the board's heartbeat failsafe takes over if nothing follows."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: HeatingControllerConfigEntry) -> None:
    """Rebuild everything after a room or an option changed."""
    await hass.config_entries.async_reload(entry.entry_id)
