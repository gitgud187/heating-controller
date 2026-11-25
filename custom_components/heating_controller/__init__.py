"""Heating Controller: owns a boiler, reads the rooms, never commands them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


@dataclass(slots=True)
class HeatingControllerData:
    """Runtime state held on the config entry while it is loaded."""


type HeatingControllerConfigEntry = ConfigEntry[HeatingControllerData]


async def async_setup_entry(hass: HomeAssistant, entry: HeatingControllerConfigEntry) -> bool:  # noqa: ARG001
    """Set up the controller from a config entry."""
    entry.runtime_data = HeatingControllerData()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HeatingControllerConfigEntry) -> bool:  # noqa: ARG001
    """Unload the controller."""
    return True
