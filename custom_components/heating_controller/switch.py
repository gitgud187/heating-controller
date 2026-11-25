"""The pause switch: boiler held off, rooms keep regulating. Survives a restart."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.helpers.restore_state import RestoreEntity

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
    """Add the pause switch."""
    async_add_entities([PauseSwitch(entry.runtime_data, entry, "pause")])


class PauseSwitch(ControllerEntity, SwitchEntity, RestoreEntity):
    """On: the boiler stays off."""

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the last position before the first evaluation acts on it."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_ON:
            self.controller.controls.paused = True

    @property
    @override
    def is_on(self) -> bool:
        """Whether heating is paused."""
        return self.controller.controls.paused

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Pause."""
        await self.controller.async_set_paused(paused=True)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Resume."""
        await self.controller.async_set_paused(paused=False)
