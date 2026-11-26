from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.heating_controller.engine.config import EngineConfig
from custom_components.heating_controller.engine.demand import (
    RoomAssessment,
    RoomState,
    RoomStatus,
    assess_room,
    needs_heat,
)
from custom_components.heating_controller.engine.model import RoomSnapshot, ThermostatMode

from .factories import NOW, moes, room, sonoff

CONFIG = EngineConfig()
OLD = NOW - timedelta(minutes=20)


def assess(snap: RoomSnapshot, previous: RoomState | None = None) -> RoomAssessment:
    return assess_room(snap, previous or RoomState(), NOW, CONFIG)


@pytest.mark.parametrize(
    ("previous", "error_k", "expected"),
    [
        (False, 0.2, True),
        (False, 0.19, False),
        (True, 0.19, True),
        (True, 0.0, False),
        (True, -0.5, False),
        (False, 0.1, False),
    ],
)
def test_hysteresis(
    previous: bool,  # noqa: FBT001 - parametrized
    error_k: float,
    expected: bool,  # noqa: FBT001 - parametrized
) -> None:
    assert needs_heat(previous=previous, error_k=error_k, needs_heat_k=0.2) is expected


def test_sensor_steps_of_a_tenth_reach_the_threshold() -> None:
    result = assess(room(target=21.0, wall=20.8, valve=moes()))
    assert result.needs_heat
    assert result.error_k == 0.2


def test_served_room_contributes_area_times_error() -> None:
    result = assess(room(area_m2=10.0, target=21.0, wall=20.0))
    assert result.status is RoomStatus.SERVED
    assert result.served
    assert not result.blocked
    assert not result.excluded
    assert result.load_m2k == pytest.approx(10.0)
    assert result.state == RoomState(needs_heat=True)


def test_satisfied_room_has_no_load() -> None:
    result = assess(room(target=21.0, wall=21.0))
    assert result.status is RoomStatus.SATISFIED
    assert result.load_m2k == 0.0
    assert result.state == RoomState()


def test_hysteresis_band_holds_previous_demand() -> None:
    held = assess(room(target=21.0, wall=20.9), RoomState(needs_heat=True))
    assert held.status is RoomStatus.SERVED
    fresh = assess(room(target=21.0, wall=20.9))
    assert fresh.status is RoomStatus.SATISFIED


@pytest.mark.parametrize(
    ("snap", "status"),
    [
        (room(wall=None), RoomStatus.WALL_INVALID),
        (room(target=None), RoomStatus.THERMOSTAT_INVALID),
        (room(mode=None), RoomStatus.THERMOSTAT_INVALID),
    ],
)
def test_invalid_data_excludes_and_keeps_state(snap: RoomSnapshot, status: RoomStatus) -> None:
    previous = RoomState(needs_heat=True, blocked_since=OLD)
    result = assess(snap, previous)
    assert result.status is status
    assert result.excluded
    assert result.state == previous
    assert result.error_k is None
    assert result.load_m2k == 0.0


def test_stale_wall_sensor_excludes() -> None:
    snap = room()
    stale = RoomSnapshot(
        name=snap.name,
        area_m2=snap.area_m2,
        thermostat=snap.thermostat,
        wall=type(snap.wall)(20.0, NOW - timedelta(minutes=20)),
        valve=snap.valve,
    )
    assert assess(stale).status is RoomStatus.WALL_INVALID


@pytest.mark.parametrize(
    ("snap", "status"),
    [
        (room(mode=ThermostatMode.OFF), RoomStatus.THERMOSTAT_OFF),
        (room(window_open=True), RoomStatus.WINDOW_OPEN),
    ],
)
def test_no_demand_resets_state(snap: RoomSnapshot, status: RoomStatus) -> None:
    result = assess(snap, RoomState(needs_heat=True, blocked_since=OLD))
    assert result.status is status
    assert not result.excluded
    assert not result.needs_heat
    assert result.state == RoomState()
    assert result.load_m2k == 0.0


@pytest.mark.parametrize(
    ("snap", "status"),
    [
        (room(valve=moes(heating=None, position=None)), RoomStatus.VALVE_DATA_MISSING),
        (room(valve=sonoff(heating=None)), RoomStatus.VALVE_DATA_MISSING),
    ],
)
def test_valve_problems_fail_closed_but_track_demand(
    snap: RoomSnapshot, status: RoomStatus
) -> None:
    result = assess(snap, RoomState(blocked_since=OLD))
    assert result.status is status
    assert result.excluded
    assert result.needs_heat
    assert not result.served
    assert result.load_m2k == 0.0
    assert result.state == RoomState(needs_heat=True, blocked_since=OLD)


def test_closed_valve_counts_as_served_during_grace() -> None:
    snap = room(valve=moes(heating=False, position=0.0))
    first = assess(snap)
    assert first.status is RoomStatus.WAITING_FOR_VALVE
    assert first.served
    assert first.state.blocked_since == NOW

    at_limit = assess(snap, RoomState(needs_heat=True, blocked_since=NOW - timedelta(minutes=15)))
    assert at_limit.status is RoomStatus.WAITING_FOR_VALVE


def test_closed_valve_blocks_after_grace() -> None:
    since = NOW - timedelta(minutes=30, seconds=1)
    result = assess(
        room(valve=moes(heating=False, position=0.0)),
        RoomState(needs_heat=True, blocked_since=since),
    )
    assert result.status is RoomStatus.BLOCKED
    assert result.blocked
    assert not result.served
    assert result.load_m2k == 0.0
    assert result.state.blocked_since == since


def test_closed_valve_close_to_target_has_settled() -> None:
    # 0.3 K below target: the valve closed on its own warm sensor; not a fault, and no load.
    since = NOW - timedelta(hours=1)
    result = assess(
        room(target=20.5, wall=20.2, valve=moes(heating=False, position=0.0)),
        RoomState(needs_heat=True, blocked_since=since),
    )
    assert result.status is RoomStatus.VALVE_SETTLED
    assert not result.blocked
    assert not result.served
    assert result.load_m2k == 0.0
    assert result.state.blocked_since == since


def test_open_valve_rearms_blocked_clock() -> None:
    result = assess(room(), RoomState(needs_heat=True, blocked_since=OLD))
    assert result.state.blocked_since is None


def test_satisfied_room_rearms_blocked_clock() -> None:
    result = assess(
        room(wall=21.5, valve=moes(heating=False, position=0.0)),
        RoomState(needs_heat=True, blocked_since=OLD),
    )
    assert result.status is RoomStatus.SATISFIED
    assert result.state.blocked_since is None
