"""The snapshot: every input of one evaluation, read once by the adapter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from datetime import datetime

    from .validity import Flag, Reading


class ThermostatMode(StrEnum):
    """The room thermostat's (VTherm's) HVAC mode as far as the engine cares."""

    HEAT = "heat"
    OFF = "off"


class ValveProfile(StrEnum):
    """Which rules decide whether a valve is open and healthy."""

    MOES = "moes"
    SONOFF = "sonoff"


@dataclass(frozen=True, slots=True)
class Thermostat:
    """The room thermostat. `mode` is None when unavailable or not heat/off."""

    mode: ThermostatMode | None
    target: Reading
    window_open: bool = False


@dataclass(frozen=True, slots=True)
class MoesValve:
    """A Moes TRV801: position sensor plus `hvac_action`."""

    profile: ClassVar[ValveProfile] = ValveProfile.MOES

    heating: Flag
    position: Reading
    preset: str | None
    mode: str | None


@dataclass(frozen=True, slots=True)
class SonoffValve:
    """A Sonoff TRVZB: no position, only `hvac_action`."""

    profile: ClassVar[ValveProfile] = ValveProfile.SONOFF

    heating: Flag
    system_mode: str | None
    opening_degree_pct: float | None
    closing_degree_pct: float | None
    smart_temperature_control: str | None
    temperature_sensor_select: str | None


type Valve = MoesValve | SonoffValve


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
    run_now: bool = False
