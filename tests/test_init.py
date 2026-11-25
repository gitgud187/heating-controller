from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState

from .adapter.conftest import Calls, set_plant, setup

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_setup_unload_and_reload_on_a_new_room(hass: HomeAssistant, calls: Calls) -> None:
    set_plant(hass)
    entry = await setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    first = entry.runtime_data

    room = next(iter(entry.subentries.values()))
    hass.config_entries.async_update_subentry(entry, room, title="Guest room")
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not first

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    unloaded = hass.config_entries.async_get_entry(entry.entry_id)
    assert unloaded is not None
    assert unloaded.state is ConfigEntryState.NOT_LOADED
