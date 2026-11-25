"""
The thermal plant: one node per room, radiators with a lag, a boiler with a 30 % floor.

Units: temperatures in °C, powers in W, capacities in J/K, time steps as timedelta. The model
is deliberately simple; its parameters are guesses that only have to be plausible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

BOILER_FLOOR_PCT = 30


@dataclass(frozen=True, slots=True)
class RoomParams:
    """
    One room's thermal node.

    `radiator_w` is what the radiator gives at 100 % boiler output with the valve fully open;
    the gain scales with valve opening x output. `radiator_lag` is the first-order time
    constant of the radiator's water and metal.
    """

    name: str
    area_m2: float
    loss_w_per_k: float
    capacity_j_per_k: float
    radiator_w: float
    radiator_lag: timedelta = timedelta(minutes=10)
    internal_gain_w: float = 0.0
    window_loss_w_per_k: float = 30.0


@dataclass(frozen=True, slots=True)
class Coupling:
    """Heat exchange between two neighbouring rooms (internal wall, door)."""

    room_a: str
    room_b: str
    w_per_k: float


@dataclass(frozen=True, slots=True)
class OutdoorProfile:
    """A daily sine around `mean_c`, coldest at `coldest_hour` (hours of the day, UTC)."""

    mean_c: float
    amplitude_k: float = 0.0
    coldest_hour: float = 5.0

    def at(self, moment: datetime) -> float:
        """Outdoor temperature at a moment."""
        hours = moment.hour + moment.minute / 60 + moment.second / 3600
        phase = 2 * math.pi * (hours - self.coldest_hour) / 24
        return self.mean_c - self.amplitude_k * math.cos(phase)


def effective_output_pct(*, heating: bool, request_pct: float) -> float:
    """Return what the burner really does: off, or the request above the 30 % floor."""
    if not heating:
        return 0.0
    return max(float(BOILER_FLOOR_PCT), min(100.0, request_pct))


class ThermalPlant:
    """The rooms' temperatures and radiator outputs, advanced by explicit Euler steps."""

    def __init__(
        self,
        rooms: tuple[RoomParams, ...],
        couplings: tuple[Coupling, ...],
        initial_c: Mapping[str, float],
    ) -> None:
        """Start every room at its initial temperature with cold radiators."""
        self.rooms = {room.name: room for room in rooms}
        self.couplings = couplings
        self.temps_c = {room.name: initial_c[room.name] for room in rooms}
        self.radiator_w = dict.fromkeys(self.rooms, 0.0)

    def step(
        self,
        dt: timedelta,
        *,
        outdoor_c: float,
        output_pct: float,
        openings: Mapping[str, float],
        windows_open: Mapping[str, bool],
    ) -> None:
        """
        Advance by `dt`.

        `output_pct` is the effective boiler output (0 when off), `openings` the valve
        opening per room as a fraction 0..1.
        """
        seconds = dt.total_seconds()
        flows = dict.fromkeys(self.rooms, 0.0)
        for coupling in self.couplings:
            flow = coupling.w_per_k * (
                self.temps_c[coupling.room_a] - self.temps_c[coupling.room_b]
            )
            flows[coupling.room_a] -= flow
            flows[coupling.room_b] += flow
        for name, room in self.rooms.items():
            temp = self.temps_c[name]
            target_w = room.radiator_w * openings.get(name, 0.0) * output_pct / 100
            lag = room.radiator_lag.total_seconds()
            self.radiator_w[name] += (target_w - self.radiator_w[name]) * (
                1 - math.exp(-seconds / lag)
            )
            loss = room.loss_w_per_k
            if windows_open.get(name, False):
                loss += room.window_loss_w_per_k
            power = (
                self.radiator_w[name] + room.internal_gain_w - loss * (temp - outdoor_c)
            ) + flows[name]
            self.temps_c[name] = temp + power * seconds / room.capacity_j_per_k
