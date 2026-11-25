from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from custom_components.heating_controller.engine.config import EngineConfig
from custom_components.heating_controller.engine.demand import RoomStatus
from custom_components.heating_controller.engine.evaluate import Command, Evaluation, evaluate
from custom_components.heating_controller.engine.guard import LoadSample
from custom_components.heating_controller.engine.model import Controls
from custom_components.heating_controller.engine.reasons import ReasonCode
from custom_components.heating_controller.engine.state import EngineState

from .factories import NOW, moes, room, snapshot

if TYPE_CHECKING:
    from collections.abc import Callable

    from custom_components.heating_controller.engine.model import RoomSnapshot

    type Rooms = Callable[[datetime], tuple[RoomSnapshot, ...]]

CONFIG = EngineConfig()


def cold(at: datetime) -> tuple[RoomSnapshot, ...]:
    """Load 10 m²·K: start."""
    return (room("bedroom", area_m2=10.0, wall=20.0, at=at),)


def mild(at: datetime) -> tuple[RoomSnapshot, ...]:
    """Load 2 m²·K: below start, above stop."""
    return (room("bedroom", area_m2=10.0, wall=20.8, at=at),)


def warm(at: datetime) -> tuple[RoomSnapshot, ...]:
    """Satisfied: load 0, valve closed."""
    return (room("bedroom", wall=21.5, valve=moes(heating=False, position=0.0, at=at), at=at),)


def warm_open(at: datetime) -> tuple[RoomSnapshot, ...]:
    """Satisfied, valve still open: load 0 without tripping the no-flow guard."""
    return (room("bedroom", wall=21.5, at=at),)


def closed_valve(at: datetime) -> tuple[RoomSnapshot, ...]:
    """Needs heat, valve closed: served during the grace, no open valve."""
    return (room("bedroom", wall=20.0, valve=moes(heating=False, position=0.0, at=at), at=at),)


def invalid(at: datetime) -> tuple[RoomSnapshot, ...]:
    """Every room excluded."""
    return (room("bedroom", wall=None, at=at),)


class Run:
    """Drives the engine minute by minute, like the adapter would."""

    def __init__(self, state: EngineState | None = None) -> None:
        self.now = NOW
        self.state = state or EngineState()

    def step(  # noqa: PLR0913 - a test driver with every input as a keyword
        self,
        rooms: Rooms,
        controls: Controls | None = None,
        *,
        boiler_unavailable: bool = False,
        observed_off: bool = False,
        complete: bool = True,
        outdoor: float | None = 0.0,
    ) -> Evaluation:
        snap = snapshot(
            *rooms(self.now),
            now=self.now,
            outdoor=outdoor,
            boiler_on=None if boiler_unavailable else self.state.boiler_on and not observed_off,
            complete=complete,
        )
        result = evaluate(snap, self.state, CONFIG, controls or Controls())
        self.state = result.state
        self.now += timedelta(minutes=1)
        return result

    def run(self, rooms: Rooms, steps: int, controls: Controls | None = None) -> Evaluation:
        result = self.step(rooms, controls)
        for _ in range(steps - 1):
            result = self.step(rooms, controls)
        return result


def test_cold_room_starts_the_boiler() -> None:
    result = Run().step(cold)
    assert result.command == Command(heat=True, modulation_pct=result.modulation.output_pct)
    assert result.reason.code is ReasonCode.START_LOAD
    assert result.reasons == (result.reason,)
    assert result.load_m2k == pytest.approx(10.0)
    assert result.state.on_since == NOW
    assert result.state.starts == (NOW,)
    assert result.starts_last_hour == 1
    assert result.rooms[0].status is RoomStatus.SERVED
    assert result.state.modulation_pct == result.modulation.output_pct


def test_mild_room_waits() -> None:
    result = Run().step(mild)
    assert result.command == Command(heat=False, modulation_pct=CONFIG.guard.idle_pct)
    assert result.reason.code is ReasonCode.WAITING_LOAD
    assert result.state.starving_since == {"bedroom": NOW}


def test_starvation_start_after_45_min() -> None:
    run = Run()
    assert not run.run(mild, 46).command.heat  # type: ignore[union-attr]  # boiler available
    result = run.step(mild)
    assert result.reason.code is ReasonCode.START_STARVATION
    assert result.state.starving_since == {}


def test_minimum_on_then_stop() -> None:
    run = Run()
    run.step(cold)
    held = run.run(warm_open, 9)
    assert held.reason.code is ReasonCode.RUNNING_MIN_ON
    stopped = run.step(warm_open)
    assert stopped.reason.code is ReasonCode.STOP_LOAD
    assert stopped.command == Command(heat=False, modulation_pct=CONFIG.guard.idle_pct)
    assert stopped.state.last_off == NOW + timedelta(minutes=10)
    assert stopped.state.on_since is None


def test_guard_reason_is_appended() -> None:
    result = Run().step(cold, Controls(paused=True))
    assert not result.command.heat  # type: ignore[union-attr]  # boiler available
    assert [r.code for r in result.reasons] == [ReasonCode.START_LOAD, ReasonCode.PAUSED]
    assert result.reason.code is ReasonCode.PAUSED


def test_incomplete_snapshot_blocks_start() -> None:
    result = Run().step(cold, complete=False)
    assert result.reason.code is ReasonCode.SNAPSHOT_INCOMPLETE


def test_boiler_unavailable_commands_nothing() -> None:
    run = Run()
    run.step(cold)
    result = run.step(cold, boiler_unavailable=True)
    assert result.command is None
    assert result.reason.code is ReasonCode.BOILER_UNAVAILABLE
    assert not result.state.boiler_on
    assert result.state.last_off == NOW + timedelta(minutes=1)
    assert result.state.modulation_pct is None


def test_satisfied_rooms_with_closed_valves_stop_a_run_before_minimum_on() -> None:
    run = Run()
    run.step(cold)
    run.run(warm, 3)
    result = run.step(warm)
    assert result.reason.code is ReasonCode.NO_OPEN_VALVE


def test_no_open_valve_stops_a_run() -> None:
    run = Run()
    run.step(cold)
    run.run(closed_valve, 3)
    result = run.step(closed_valve)
    assert result.reason.code is ReasonCode.NO_OPEN_VALVE
    assert not result.command.heat  # type: ignore[union-attr]  # boiler available
    assert result.state.no_open_valve_since is None


def warming(k_per_hour: float, until: timedelta | None = None) -> Rooms:
    """The bedroom from 18 °C towards 21 °C at a steady rate, flat after `until`."""

    def rooms(at: datetime) -> tuple[RoomSnapshot, ...]:
        elapsed = at - NOW if until is None else min(at - NOW, until)
        return (room("bedroom", area_m2=10.0, wall=18.0 + k_per_hour * (elapsed / HOUR), at=at),)

    return rooms


HOUR = timedelta(hours=1)


def test_stalled_run_locks_out_for_an_hour_until_cleared() -> None:
    run = Run()
    running = run.run(cold, 180)
    assert running.command.heat  # type: ignore[union-attr]  # boiler available
    assert len(running.state.run_loads) == 71
    stopped = run.step(cold)
    assert stopped.reason.code is ReasonCode.NO_PROGRESS
    assert stopped.reason.message == (
        "stopped: ran 3 h, load not falling (10.0 → 10.0 m²·K in 60 min), locked out until 16:00"
    )
    assert stopped.state.run_loads == ()
    lockout = stopped.state.lockout_until
    assert lockout == run.now - timedelta(minutes=1) + timedelta(hours=1)
    waiting = run.run(cold, 30)
    assert waiting.reason.code is ReasonCode.LOCKOUT
    cleared = run.step(cold, Controls(clear_lockout=True))
    assert cleared.command.heat  # type: ignore[union-attr]  # boiler available
    assert cleared.state.lockout_until is None
    assert cleared.state.run_loads == (LoadSample(cleared.state.on_since, 10.0),)  # type: ignore[arg-type]  # on since the start


def _first_stop(run: Run, rooms: Rooms, limit: int) -> tuple[int, Evaluation]:
    for minute in range(limit):
        result = run.step(rooms)
        if not result.command.heat:  # type: ignore[union-attr]  # boiler available
            return minute, result
    msg = "no stop"
    raise AssertionError(msg)


def test_progress_keeps_a_run_going_until_the_absolute_limit() -> None:
    minute, stopped = _first_stop(Run(), warming(0.1), 400)
    assert minute == 360
    assert stopped.reason.code is ReasonCode.MAX_RUN
    assert stopped.reason.message == "stopped: ran 6 h, locked out until 19:00"


def test_a_stall_after_progress_is_caught_when_it_happens() -> None:
    minute, stopped = _first_stop(Run(), warming(0.2, until=timedelta(minutes=225)), 400)
    assert 255 < minute <= 271
    assert stopped.reason.code is ReasonCode.NO_PROGRESS
    assert stopped.reason.message.startswith("stopped: ran 4 h ")


def test_a_restart_gap_delays_the_progress_verdict() -> None:
    run = Run()
    run.run(cold, 106)  # samples up to minute 105
    run.state = EngineState.from_dict(run.state.to_dict())
    run.now += timedelta(minutes=20)  # Home Assistant down, the board kept the boiler on
    minute, stopped = _first_stop(run, cold, 100)
    assert minute + 126 == 186  # no sample 60 to 70 min old from minute 180 to 185
    assert stopped.reason.code is ReasonCode.NO_PROGRESS


def test_all_rooms_excluded_stops_immediately() -> None:
    run = Run()
    run.step(cold)
    result = run.step(invalid)
    assert result.reason.code is ReasonCode.ALL_ROOMS_EXCLUDED
    assert not result.command.heat  # type: ignore[union-attr]  # boiler available


def test_no_rooms_means_off() -> None:
    assert Run().step(lambda _at: ()).reason.code is ReasonCode.ALL_ROOMS_EXCLUDED


def test_invalid_outdoor_falls_back() -> None:
    assert Run().step(cold, outdoor=None).modulation.outdoor_fallback


def test_state_survives_a_restart_mid_run() -> None:
    run = Run()
    run.run(cold, 5)
    restored = Run(EngineState.from_dict(run.state.to_dict()))
    restored.now = run.now
    assert restored.state == run.state
    result = restored.step(warm_open)
    assert result.reason.code is ReasonCode.RUNNING_MIN_ON


def test_a_stop_by_the_board_starts_the_minimum_off() -> None:
    # The board's heartbeat failsafe switched the boiler off while the engine was not running.
    run = Run()
    run.run(cold, 5)
    stopped_at = run.now
    result = run.step(cold, observed_off=True)
    assert result.reasons[0].code is ReasonCode.EXTERNAL_STOP
    assert result.reason.code is ReasonCode.MIN_OFF
    assert result.command == Command(heat=False, modulation_pct=CONFIG.guard.idle_pct)
    assert result.state.on_since is None
    assert result.state.run_loads == ()
    assert result.state.last_off == stopped_at
    waiting = run.run(cold, 7)
    assert waiting.reason.code is ReasonCode.MIN_OFF
    restarted = run.step(cold)
    assert restarted.command.heat  # type: ignore[union-attr]  # boiler available
    assert restarted.state.starts == (NOW, stopped_at + timedelta(minutes=8))


def test_switch_still_off_right_after_a_start_is_not_a_stop() -> None:
    run = Run()
    run.step(cold)
    snap = snapshot(*cold(NOW), now=NOW + timedelta(seconds=10), boiler_on=False)
    result = evaluate(snap, run.state, CONFIG, Controls())
    assert ReasonCode.EXTERNAL_STOP not in [r.code for r in result.reasons]
    assert result.state.on_since == NOW
