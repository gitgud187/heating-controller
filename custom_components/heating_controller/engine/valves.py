"""
Whether heat can reach a room: the valve's sources, read generically.

A room's valve is whatever reports it: an opening in percent, a heating flag (`hvac_action`
of the valve or of the room thermostat), or both. The engine does not judge how the valve is
driven or configured; that belongs to the thermostat that drives it.

Valve data has no age limit. Radiator valves report only on change, often less than once an
hour, so an old value is normal. Data is missing only when every configured source is
unavailable or unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ValveConfig
    from .model import Valve


@dataclass(frozen=True, slots=True)
class ValveAssessment:
    """What the engine believes about one valve. A valve without data is never open."""

    open: bool
    data_missing: bool


def assess_valve(valve: Valve, config: ValveConfig) -> ValveAssessment:
    """Open when any source says so; missing when no source has a value."""
    heating = None if valve.heating is None else valve.heating.value
    position = None if valve.position is None else valve.position.value
    position_open = position is not None and position > config.open_position_pct
    return ValveAssessment(
        open=position_open or heating is True,
        data_missing=heating is None and position is None,
    )
