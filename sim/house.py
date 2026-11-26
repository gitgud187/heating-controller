"""
The default house: four rooms with plausible, made-up parameters.

Sized so that 30 % output is more than any room needs in mild weather and about 70 % is
needed around -5 °C: each radiator gives its room's loss at 26 K (21 °C inside, -5 °C out)
at 70 % output with the valve fully open, a little more in the bathroom.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING

from .devices import Schedule, SensorParams, ValveKind, ValveParams
from .plant import Coupling, RoomParams

if TYPE_CHECKING:
    from collections.abc import Callable

DESIGN_DELTA_K = 26.0
DESIGN_OUTPUT = 0.7
TIME_CONSTANT = timedelta(hours=5)


@dataclass(frozen=True, slots=True)
class RoomSpec:
    """One simulated room: thermal node, wall sensor, valve and schedule."""

    params: RoomParams
    sensor: SensorParams
    valve: ValveParams
    schedule: Schedule


@dataclass(frozen=True, slots=True)
class HouseSpec:
    """Rooms plus the couplings between them."""

    rooms: tuple[RoomSpec, ...]
    couplings: tuple[Coupling, ...] = ()

    def room(self, name: str) -> RoomSpec:
        """Look a room up by name."""
        for spec in self.rooms:
            if spec.params.name == name:
                return spec
        msg = f"no room {name!r}"
        raise KeyError(msg)

    def with_room(self, name: str, change: Callable[[RoomSpec], RoomSpec]) -> HouseSpec:
        """Return a copy with one room changed."""
        self.room(name)
        rooms = tuple(change(spec) if spec.params.name == name else spec for spec in self.rooms)
        return HouseSpec(rooms, self.couplings)

    def with_schedule(self, schedule: Schedule) -> HouseSpec:
        """Return a copy where every room follows the same schedule."""
        return HouseSpec(
            tuple(replace(spec, schedule=schedule) for spec in self.rooms), self.couplings
        )


def room_params(  # noqa: PLR0913 - every parameter is a keyword with a default
    name: str,
    area_m2: float,
    *,
    loss_w_per_k: float,
    radiator_factor: float = 1.0,
    internal_gain_w: float = 0.0,
    radiator_lag: timedelta = timedelta(minutes=10),
) -> RoomParams:
    """Size a room by the design rule in the module docstring."""
    return RoomParams(
        name=name,
        area_m2=area_m2,
        loss_w_per_k=loss_w_per_k,
        capacity_j_per_k=loss_w_per_k * TIME_CONSTANT.total_seconds(),
        radiator_w=loss_w_per_k * DESIGN_DELTA_K / DESIGN_OUTPUT * radiator_factor,
        radiator_lag=radiator_lag,
        internal_gain_w=internal_gain_w,
    )


SLOW_SENSOR = SensorParams(interval=timedelta(minutes=5), change_k=0.2)
FAST_SENSOR = SensorParams(interval=timedelta(minutes=1))
MOES = ValveParams(kind=ValveKind.PROPORTIONAL)
SONOFF = ValveParams(kind=ValveKind.ON_OFF)


def schedule(night: float, morning: float, day: float) -> Schedule:
    """Night from 22:00, morning from 06:00, day from 08:30."""
    return Schedule(points=((6.0, morning), (8.5, day), (22.0, night)))


def default_house() -> HouseSpec:
    """Bathroom, kitchen, bedroom and living room; the living room has the on/off valve."""
    return HouseSpec(
        rooms=(
            RoomSpec(
                room_params(
                    "bathroom",
                    4.7,
                    loss_w_per_k=15.0,
                    radiator_factor=1.2,
                    internal_gain_w=30.0,
                    radiator_lag=timedelta(minutes=8),
                ),
                SLOW_SENSOR,
                MOES,
                schedule(night=19.0, morning=22.0, day=21.0),
            ),
            RoomSpec(
                room_params("kitchen", 5.6, loss_w_per_k=18.0, internal_gain_w=100.0),
                SLOW_SENSOR,
                MOES,
                schedule(night=18.0, morning=21.0, day=21.0),
            ),
            RoomSpec(
                room_params(
                    "bedroom",
                    16.2,
                    loss_w_per_k=40.0,
                    internal_gain_w=60.0,
                    radiator_lag=timedelta(minutes=12),
                ),
                FAST_SENSOR,
                MOES,
                schedule(night=18.0, morning=20.0, day=19.0),
            ),
            RoomSpec(
                room_params(
                    "living_room",
                    17.8,
                    loss_w_per_k=45.0,
                    internal_gain_w=150.0,
                    radiator_lag=timedelta(minutes=15),
                ),
                FAST_SENSOR,
                SONOFF,
                schedule(night=18.0, morning=21.0, day=21.0),
            ),
        ),
        couplings=(
            Coupling("kitchen", "living_room", 20.0),
            Coupling("bedroom", "living_room", 10.0),
            Coupling("bathroom", "bedroom", 8.0),
        ),
    )
