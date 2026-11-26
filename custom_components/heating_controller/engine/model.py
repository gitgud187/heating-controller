"""The snapshot: every input of one evaluation, read once by the adapter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from .validity import Flag, Reading


class ThermostatMode(StrEnum):
    """The room thermostat's (VTherm's) HVAC mode as far as the engine cares."""

    HEAT = "heat"
    OFF = "off"


@dataclass(frozen=True, slots=True)
class Thermostat:
    """The room thermostat. `mode` is None when unavailable or not heat/off."""

    mode: ThermostatMode | None
    target: Reading
    window_open: bool = False


@dataclass(frozen=True, slots=True)
class Valve:
    """
    How the engine sees whether heat reaches the room. Every source is optional.

    `heating` is a heating flag (the valve's or the thermostat's `hvac_action`), `position`
    an opening in percent. A source that is not configured is None; a configured source that
    is unavailable has a None value.
    """

    heating: Flag | None = None
    position: Reading | None = None


@dataclass(frozen=True, slots=True)
class RoomSnapshot:
    """One room: its thermostat, wall sensor and valve."""

    name: str
    area_m2: float
    thermostat: Thermostat
    wall: Reading
    valve: Valve

    def __post_init__(self) -> None:
        """Reject an area that would break the weighting."""
        if self.area_m2 <= 0:
            msg = f"room {self.name!r}: area must be positive, got {self.area_m2}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """
    All inputs of one evaluation.

    `now` is the evaluation time, `taken_at` when the adapter read the states. `complete` is
    False when the adapter could not resolve every configured entity. `boiler_on` is the
    observed switch state, None when the boiler entities are unavailable.
    """

    now: datetime
    taken_at: datetime
    complete: bool
    rooms: tuple[RoomSnapshot, ...]
    outdoor: Reading
    boiler_on: bool | None

    def __post_init__(self) -> None:
        """Reject duplicate room names; the engine state is keyed by name."""
        names = [room.name for room in self.rooms]
        if len(names) != len(set(names)):
            msg = f"duplicate room names: {sorted(names)}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Controls:
    """The user controls. Buttons are True on the evaluation after they were pressed."""

    paused: bool = False
    clear_lockout: bool = False
