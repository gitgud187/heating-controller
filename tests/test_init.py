from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant.common import MockConfigEntry

from custom_components.heating_controller.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_setup_and_unload(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    unloaded = hass.config_entries.async_get_entry(entry.entry_id)
    assert unloaded is not None
    assert unloaded.state is ConfigEntryState.NOT_LOADED
