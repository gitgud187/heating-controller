"""Builders for engine inputs with sensible, healthy defaults."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.heating_controller.engine.model import (
    MoesValve,
    RoomSnapshot,
    Snapshot,
    SonoffValve,
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
    preset: str | None = "manual",
    mode: str | None = "comfort",
    at: datetime = NOW,
) -> MoesValve:
    return MoesValve(
        heating=Flag(heating, at),
        position=Reading(position, at),
        preset=preset,
        mode=mode,
    )


def sonoff(  # noqa: PLR0913 - a builder is clearer with every knob as a keyword
    *,
    heating: bool | None = True,
    system_mode: str | None = "heat",
    opening: float | None = 100.0,
    closing: float | None = 100.0,
    smart: str | None = "off",
    sensor_select: str | None = "local_temperature",
    at: datetime = NOW,
) -> SonoffValve:
    return SonoffValve(
        heating=Flag(heating, at),
        system_mode=system_mode,
        opening_degree_pct=opening,
        closing_degree_pct=closing,
        smart_temperature_control=smart,
        temperature_sensor_select=sensor_select,
    )


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
