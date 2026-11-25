"""The clear-lockout button: ends a no-progress or max-run lockout early."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from homeassistant.components.button import ButtonEntity

from .entity import ControllerEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import HeatingControllerConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - platform signature
    entry: HeatingControllerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the clear-lockout button."""
    async_add_entities([ClearLockoutButton(entry.runtime_data, entry, "clear_lockout")])


class ClearLockoutButton(ControllerEntity, ButtonEntity):
    """Pressed: the lockout ends at the next evaluation, which runs at once."""

    @override
    async def async_press(self) -> None:
        """End the lockout."""
        await self.controller.async_clear_lockout()
