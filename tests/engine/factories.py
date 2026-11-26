"""Builders for engine inputs with sensible, healthy defaults."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.heating_controller.engine.model import (
    RoomSnapshot,
    Snapshot,
    Thermostat,
    ThermostatMode,
    Valve,
)
from custom_components.heating_controller.engine.validity import Flag, Reading

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def moes(
    *,
    heating: bool | None = True,
    position: float | None = 50.0,
    at: datetime = NOW,
) -> Valve:
    """A valve that reports both a heating flag and a position (like a Moes TRV801)."""
    return Valve(heating=Flag(heating, at), position=Reading(position, at))


def sonoff(*, heating: bool | None = True, at: datetime = NOW) -> Valve:
    """A valve that reports only a heating flag (like a Sonoff TRVZB)."""
    return Valve(heating=Flag(heating, at))


def room(  # noqa: PLR0913 - a builder is clearer with every knob as a keyword
    name: str = "bedroom",
    *,
    area_m2: float = 10.0,
    target: float | None = 21.0,
    wall: float | None = 20.0,
    mode: ThermostatMode | None = ThermostatMode.HEAT,
    window_open: bool = False,
    valve: Valve | None = None,
    at: datetime = NOW,
) -> RoomSnapshot:
    return RoomSnapshot(
        name=name,
        area_m2=area_m2,
        thermostat=Thermostat(mode=mode, target=Reading(target, at), window_open=window_open),
        wall=Reading(wall, at),
        valve=valve if valve is not None else moes(at=at),
    )


def snapshot(
    *rooms: RoomSnapshot,
    now: datetime = NOW,
    outdoor: float | None = 0.0,
    boiler_on: bool | None = False,
    complete: bool = True,
    taken_at: datetime | None = None,
) -> Snapshot:
    return Snapshot(
        now=now,
        taken_at=taken_at if taken_at is not None else now,
        complete=complete,
        rooms=rooms,
        outdoor=Reading(outdoor, now),
        boiler_on=boiler_on,
    )
