"""
The scenarios of spec 15: each function returns a scenario with its own checks.

Every scenario is also judged on the spec 9 guard limits and the spec 13 criteria (see
`metrics.assess`); the checks here are what is specific to the situation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from custom_components.heating_controller.engine.demand import RoomStatus
from custom_components.heating_controller.engine.reasons import ReasonCode

from .devices import FailsafeCause, Schedule, SensorFault, ValveFault
from .house import default_house
from .loop import (
    ALL_ROOMS,
    Button,
    Criteria,
    Outage,
    OutageEvent,
    PauseEvent,
    Press,
    Scenario,
    ValveFaultEvent,
    WallSensorFault,
    WindowOpen,
)
from .metrics import MINUTE, find_runs
from .plant import OutdoorProfile

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from .loop import RoomSample, Sample, SimResult

MIDNIGHT = datetime(2026, 1, 14, 0, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def _hours(hours: float) -> timedelta:
    return timedelta(hours=hours)


def _between(result: SimResult, start: timedelta, end: timedelta) -> Iterator[Sample]:
    begin = result.scenario.start + start
    stop = result.scenario.start + end
    return (sample for sample in result.samples if begin <= sample.time < stop)


def _at(result: SimResult, offset: timedelta) -> Sample:
    moment = result.scenario.start + offset
    return next(sample for sample in result.samples if sample.time >= moment)


def _room(sample: Sample, name: str) -> RoomSample:
    return next(room for room in sample.rooms if room.name == name)


def _offset(result: SimResult, sample: Sample) -> str:
    return f"{(sample.time - result.scenario.start) / MINUTE:.0f} min"


# --- weather --------------------------------------------------------------------------------


def cold_night() -> Scenario:
    """Simulate a full day around -5 °C, with the evening setback and the morning warm-up."""
    return Scenario(
        name="cold_night",
        description="24 h around -5 °C",
        start=MIDNIGHT + _hours(18),
        duration=DAY,
        outdoor=OutdoorProfile(mean_c=-5.0, amplitude_k=1.5),
        house=default_house(),
    )


def mild_day() -> Scenario:
    """Simulate a full day around +10 °C: 30 % is more than any room needs."""
    return Scenario(
        name="mild_day",
        description="24 h around +10 °C (6 to 14 °C)",
        start=MIDNIGHT,
        duration=DAY,
        outdoor=OutdoorProfile(mean_c=10.0, amplitude_k=4.0),
        house=default_house(),
    )


def _warm_by(raised: timedelta, deadline: timedelta) -> Callable[[SimResult], list[str]]:
    """Every room comes within 0.1 K of its raised target between `raised` and `deadline`."""

    def check(result: SimResult) -> list[str]:
        warm: set[str] = set()
        for sample in _between(result, raised, deadline):
            warm.update(room.name for room in sample.rooms if room.temp_c >= room.target_c - 0.1)
        names = [spec.params.name for spec in result.scenario.house.rooms]
        late = deadline - raised
        return [
            f"{name} not warm within {late / MINUTE:.0f} min" for name in names if name not in warm
        ]

    return check


def morning_warmup() -> Scenario:
    """Set back overnight at 0 °C, raise every room at 06:00; warm within 2 h."""
    return Scenario(
        name="morning_warmup",
        description="setback night at 0 °C, targets up at 06:00, warm within 2 h",
        start=MIDNIGHT,
        duration=_hours(14),
        outdoor=OutdoorProfile(mean_c=0.0, amplitude_k=2.0),
        house=default_house(),
        checks=(_warm_by(_hours(6), _hours(8)),),
    )


# --- rooms ----------------------------------------------------------------------------------

WINDOW_AT = _hours(10)
WINDOW_FOR = timedelta(minutes=20)


def _window_checks(result: SimResult) -> list[str]:
    failures = []
    for sample in _between(result, WINDOW_AT, WINDOW_AT + WINDOW_FOR):
        room = _room(sample, "living_room")
        if sample.evaluated and room.status is not RoomStatus.WINDOW_OPEN:
            failures.append(
                f"living room {room.status} with the window open at {sample.time:%H:%M}"
            )
        if room.load_m2k:
            failures.append(f"living room adds load with the window open at {sample.time:%H:%M}")
    recovered = _room(_at(result, WINDOW_AT + WINDOW_FOR + _hours(2)), "living_room")
    if recovered.target_c - recovered.temp_c > 0.5:  # noqa: PLR2004 - back within the cycling
        failures.append(f"living room still {recovered.target_c - recovered.temp_c:.2f} K low")
    return failures


def window_open() -> Scenario:
    """Open the living-room window for 20 min at 0 °C."""
    return Scenario(
        name="window_open",
        description="living-room window open 20 min at 0 °C",
        start=MIDNIGHT,
        duration=_hours(16),
        outdoor=OutdoorProfile(mean_c=0.0, amplitude_k=2.0),
        house=default_house(),
        events=(WindowOpen("living_room", WINDOW_AT, WINDOW_FOR),),
        checks=(_window_checks,),
    )


FAULT_AT = _hours(4)


def _status_during(
    room_name: str,
    statuses: frozenset[RoomStatus],
    start: timedelta,
    end: timedelta,
) -> Callable[[SimResult], list[str]]:
    """Check that the room has one of `statuses` and no load throughout the window."""

    def check(result: SimResult) -> list[str]:
        failures = []
        for sample in _between(result, start, end):
            room = _room(sample, room_name)
            if sample.evaluated and room.status not in statuses:
                failures.append(f"{room_name} {room.status} at {_offset(result, sample)}")
            if room.load_m2k:
                failures.append(f"{room_name} adds load at {_offset(result, sample)}")
        return failures[:3]

    return check


def valve_stuck_open() -> Scenario:
    """Hold the bedroom valve open by its preset: fail-closed, the room may overheat."""
    return Scenario(
        name="valve_stuck_open",
        description="bedroom valve held open by its preset (trap) at +5 °C",
        start=MIDNIGHT,
        duration=DAY,
        outdoor=OutdoorProfile(mean_c=5.0, amplitude_k=3.0),
        house=default_house(),
        events=(ValveFaultEvent("bedroom", FAULT_AT, ValveFault.STUCK_OPEN),),
        checks=(_status_during("bedroom", frozenset({RoomStatus.VALVE_UNHEALTHY}), FAULT_AT, DAY),),
    )


def _blocked_after_grace(room_name: str) -> Callable[[SimResult], list[str]]:
    def check(result: SimResult) -> list[str]:
        waiting = blocked = None
        for sample in _between(result, FAULT_AT, result.scenario.duration):
            status = _room(sample, room_name).status
            if status is RoomStatus.WAITING_FOR_VALVE and waiting is None:
                waiting = sample.time
            if status is RoomStatus.BLOCKED and blocked is None:
                blocked = sample.time
            if blocked is not None and _room(sample, room_name).load_m2k:
                return [f"{room_name} blocked but adds load at {sample.time:%H:%M}"]
        if waiting is None or blocked is None:
            return [f"{room_name} never blocked (waiting since {waiting})"]
        grace = (blocked - waiting) / MINUTE
        if not 15 < grace <= 17:  # noqa: PLR2004 - 15 min grace, one evaluation of slack
            return [f"{room_name} blocked after {grace:.0f} min, expected 16"]
        return []

    return check


def valve_never_opens() -> Scenario:
    """Stick the kitchen valve shut, reporting normally: blocked after 15 min."""
    return Scenario(
        name="valve_never_opens",
        description="kitchen valve never opens at 0 °C: blocked room",
        start=MIDNIGHT,
        duration=_hours(16),
        outdoor=OutdoorProfile(mean_c=0.0, amplitude_k=2.0),
        house=default_house(),
        events=(ValveFaultEvent("kitchen", FAULT_AT, ValveFault.NEVER_OPENS),),
        checks=(_blocked_after_grace("kitchen"),),
    )


SENSOR_FOR = _hours(3)


def wall_sensor_dead() -> Scenario:
    """Make the bedroom wall sensor unavailable for 3 h: room excluded at once."""
    return Scenario(
        name="wall_sensor_dead",
        description="bedroom wall sensor unavailable for 3 h at 0 °C",
        start=MIDNIGHT,
        duration=_hours(16),
        outdoor=OutdoorProfile(mean_c=0.0, amplitude_k=2.0),
        house=default_house(),
        events=(WallSensorFault("bedroom", FAULT_AT, SensorFault.DEAD, SENSOR_FOR),),
        checks=(
            _status_during(
                "bedroom",
                frozenset({RoomStatus.WALL_INVALID}),
                FAULT_AT,
                FAULT_AT + SENSOR_FOR,
            ),
        ),
    )


def _frozen_detected(room_name: str) -> Callable[[SimResult], list[str]]:
    def check(result: SimResult) -> list[str]:
        stale_min = result.scenario.config.validity.stale_after / MINUTE
        for sample in _between(result, FAULT_AT, FAULT_AT + SENSOR_FOR):
            if _room(sample, room_name).status is RoomStatus.WALL_INVALID:
                after = (sample.time - result.scenario.start - FAULT_AT) / MINUTE
                if not stale_min - 2 <= after <= stale_min:
                    return [f"{room_name} excluded {after:.0f} min after freezing"]
                return _status_during(
                    room_name,
                    frozenset({RoomStatus.WALL_INVALID}),
                    sample.time - result.scenario.start,
                    FAULT_AT + SENSOR_FOR,
                )(result)
        return [f"{room_name} never excluded while frozen"]

    return check


def wall_sensor_frozen() -> Scenario:
    """Stop the living-room wall sensor reporting for 3 h: stale after 20 min."""
    return Scenario(
        name="wall_sensor_frozen",
        description="living-room wall sensor stops reporting for 3 h at 0 °C",
        start=MIDNIGHT,
        duration=_hours(16),
        outdoor=OutdoorProfile(mean_c=0.0, amplitude_k=2.0),
        house=default_house(),
        events=(WallSensorFault("living_room", FAULT_AT, SensorFault.FROZEN, SENSOR_FOR),),
        checks=(_frozen_detected("living_room"),),
    )


# --- outages --------------------------------------------------------------------------------

Z2M_AT = _hours(6.25)
Z2M_FOR = _hours(1)


def _z2m_checks(result: SimResult) -> list[str]:
    failures = []
    if not _at(result, Z2M_AT - MINUTE).heating:
        failures.append("scenario expects the boiler to run when the valves go away")
    for sample in _between(result, Z2M_AT, Z2M_AT + Z2M_FOR):
        if sample.heating:
            failures.append(f"boiler on at {sample.time:%H:%M} without valve data")
            break
        if sample.reasons[-1] is not ReasonCode.ALL_ROOMS_EXCLUDED:
            failures.append(f"reason {sample.reasons[-1]} at {sample.time:%H:%M}")
            break
        if any(room.status is not RoomStatus.VALVE_DATA_MISSING for room in sample.rooms):
            failures.append(f"a room not fail-closed at {sample.time:%H:%M}")
            break
    return failures


def z2m_down() -> Scenario:
    """Every valve entity goes unavailable for an hour during the morning warm-up."""
    return Scenario(
        name="z2m_down",
        description="all valve entities unavailable for 1 h at -5 °C",
        start=MIDNIGHT,
        duration=_hours(16),
        outdoor=OutdoorProfile(mean_c=-5.0, amplitude_k=1.5),
        house=default_house(),
        events=(ValveFaultEvent(ALL_ROOMS, Z2M_AT, ValveFault.UNAVAILABLE, Z2M_FOR),),
        checks=(_z2m_checks,),
    )


OUTAGE_AT = _hours(6.25)


def _hung_checks(result: SimResult) -> list[str]:
    timeout = timedelta(minutes=5)
    if not _at(result, OUTAGE_AT - MINUTE).heating:
        return ["scenario expects the boiler to run when the integration hangs"]
    last_heartbeat = result.scenario.start + OUTAGE_AT - MINUTE
    off = next(
        (s for s in _between(result, OUTAGE_AT, OUTAGE_AT + _hours(1)) if not s.heating), None
    )
    if off is None:
        return ["the board never switched off"]
    waited = off.time - last_heartbeat
    if off.failsafe is not FailsafeCause.HEARTBEAT or not timeout <= waited <= timeout + MINUTE:
        return [f"board off {waited / MINUTE:.0f} min after the last heartbeat ({off.failsafe})"]
    return []


def integration_hung() -> Scenario:
    """Stop evaluating for 10 min mid-run; the board's heartbeat failsafe stops the boiler."""
    return Scenario(
        name="integration_hung",
        description="no evaluations for 10 min mid-run at -5 °C",
        start=MIDNIGHT,
        duration=_hours(14),
        outdoor=OutdoorProfile(mean_c=-5.0, amplitude_k=1.5),
        house=default_house(),
        events=(OutageEvent(Outage.HUNG, OUTAGE_AT, timedelta(minutes=10)),),
        checks=(_hung_checks,),
    )


RESTART_FOR = timedelta(minutes=2)


def _restart_checks(result: SimResult) -> list[str]:
    across = list(_between(result, OUTAGE_AT - MINUTE, OUTAGE_AT + RESTART_FOR + MINUTE))
    if not across[0].heating:
        return ["scenario expects the boiler to run when Home Assistant restarts"]
    if not all(sample.heating for sample in across):
        return ["the boiler stopped across the restart"]
    if any(sample.failsafe for sample in result.samples):
        return ["the board failsafe fired"]
    if any(sample.reasons and sample.reasons[0] is ReasonCode.EXTERNAL_STOP for sample in across):
        return ["the restored state disagreed with the boiler"]
    return []


def ha_restart() -> Scenario:
    """Home Assistant restarts mid-run: 2 min without evaluations or API."""
    return Scenario(
        name="ha_restart",
        description="Home Assistant restart mid-run (2 min gap) at -5 °C",
        start=MIDNIGHT,
        duration=_hours(14),
        outdoor=OutdoorProfile(mean_c=-5.0, amplitude_k=1.5),
        house=default_house(),
        events=(OutageEvent(Outage.RESTART, OUTAGE_AT, RESTART_FOR),),
        checks=(_restart_checks,),
    )


# --- controls -------------------------------------------------------------------------------

PAUSE_AT = _hours(6.5)
PAUSE_FOR = _hours(2)


def _pause_checks(result: SimResult) -> list[str]:
    failures = []
    if not _at(result, PAUSE_AT - MINUTE).heating:
        failures.append("scenario expects the boiler to run when paused")
    if any(sample.heating for sample in _between(result, PAUSE_AT, PAUSE_AT + PAUSE_FOR)):
        failures.append("boiler on while paused")
    after = list(_between(result, PAUSE_AT + PAUSE_FOR, PAUSE_AT + PAUSE_FOR + _hours(0.25)))
    if not any(sample.heating for sample in after):
        failures.append("no start within 15 min after the pause")
    return failures


def pause() -> Scenario:
    """Paused for 2 h during the morning warm-up at -5 °C."""
    return Scenario(
        name="pause",
        description="paused 2 h during the morning warm-up at -5 °C",
        start=MIDNIGHT,
        duration=_hours(16),
        outdoor=OutdoorProfile(mean_c=-5.0, amplitude_k=1.5),
        house=default_house(),
        events=(PauseEvent(PAUSE_AT, PAUSE_FOR),),
        checks=(_pause_checks,),
    )


RUN_NOW_AT = _hours(15.5)


GUARD_VETOES = frozenset(
    {
        ReasonCode.MIN_OFF,
        ReasonCode.START_LIMIT,
        ReasonCode.NO_OPEN_VALVE,
        ReasonCode.LOCKOUT,
        ReasonCode.PAUSED,
    }
)


def _run_now_checks(result: SimResult) -> list[str]:
    """Run now starts a 30 min run unless a guard vetoes it; the guard still applies."""
    press = result.scenario.start + RUN_NOW_AT
    sample = _at(result, RUN_NOW_AT)
    if ReasonCode.RUN_NOW not in sample.reasons:
        return [f"run now not seen: {sample.reasons}"]
    if not sample.heating:
        vetoed = sample.reasons[-1] in GUARD_VETOES
        return [] if vetoed else [f"no run after run now: {sample.reasons[-1]}"]
    run = next(run for run in find_runs(result.samples) if run.start <= press < run.end)
    if run.end - press < timedelta(minutes=30) and not run.guard_stop:
        return [f"run now ended after {(run.end - press) / MINUTE:.0f} min ({run.stop})"]
    return []


def run_now() -> Scenario:
    """Run now pressed on a mild afternoon: one run of 30 min, guards still apply."""
    return Scenario(
        name="run_now",
        description="run now pressed at 15:30 on a +10 °C day",
        start=MIDNIGHT,
        duration=_hours(18),
        outdoor=OutdoorProfile(mean_c=10.0, amplitude_k=4.0),
        house=default_house(),
        events=(Press(Button.RUN_NOW, RUN_NOW_AT),),
        checks=(_run_now_checks,),
    )


CLEAR_AT = _hours(3.5)
HOT = Schedule(points=((0.0, 24.0),))


def _lockout_checks(result: SimResult) -> list[str]:
    runs = find_runs(result.samples)
    stops = [run for run in runs if run.stop == ReasonCode.MAX_RUN]
    if not stops:
        return ["no max-run stop"]
    clear = result.scenario.start + CLEAR_AT
    stopped = stops[0].end
    later = [run for run in runs if run.start > stopped]
    if not later:
        return ["no start after clearing the lockout"]
    if later[0].start < clear:
        return [f"start at {later[0].start:%H:%M} before the lockout was cleared"]
    if later[0].start - clear > 2 * MINUTE:
        return [f"start {(later[0].start - clear) / MINUTE:.0f} min after clearing the lockout"]
    return []


def clear_lockout() -> Scenario:
    """Run into the 3 h limit with unreachable targets at -15 °C; clear the lockout early."""
    return Scenario(
        name="clear_lockout",
        description="3 h run into a lockout at -15 °C, cleared after 30 min",
        start=MIDNIGHT,
        duration=_hours(6),
        outdoor=OutdoorProfile(mean_c=-15.0),
        house=default_house().with_schedule(HOT),
        events=(Press(Button.CLEAR_LOCKOUT, CLEAR_AT),),
        settle=timedelta(0),
        initial_offset_k=-3.0,
        criteria=Criteria(max_median_error_k=None, min_distinct_pct=None, allow_max_run=True),
        checks=(_lockout_checks,),
    )


SCENARIOS: dict[str, Callable[[], Scenario]] = {
    factory.__name__: factory
    for factory in (
        cold_night,
        mild_day,
        morning_warmup,
        window_open,
        valve_stuck_open,
        valve_never_opens,
        wall_sensor_dead,
        wall_sensor_frozen,
        z2m_down,
        integration_hung,
        ha_restart,
        pause,
        run_now,
        clear_lockout,
    )
}
