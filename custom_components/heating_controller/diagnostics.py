"""Diagnostics download: configuration, engine state and the last evaluations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data

from .const import CONF_NOTIFY_SERVICE, SUBENTRY_ROOM

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import HeatingControllerConfigEntry

TO_REDACT = {CONF_NOTIFY_SERVICE}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,  # noqa: ARG001 - diagnostics signature
    entry: HeatingControllerConfigEntry,
) -> dict[str, Any]:
    """Everything needed to understand a decision after the fact."""
    controller = entry.runtime_data
    return {
        "data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "rooms": {
            room.title: dict(room.data) for room in entry.get_subentries_of_type(SUBENTRY_ROOM)
        },
        "state": controller.state.to_dict(),
        "paused": controller.controls.paused,
        "problems": sorted(controller.problems.active),
        "history": [item.as_dict() for item in controller.history],
    }
