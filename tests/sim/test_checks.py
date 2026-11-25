"""Each scenario's own checks fail when the record shows the wrong behaviour."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from functools import cache

from custom_components.heating_controller.engine.demand import RoomStatus
from custom_components.heating_controller.engine.reasons import ReasonCode
from sim import scenarios as sc
from sim.devices import FailsafeCause
from sim.loop import Sample, SimResult, simulate

MINUTE = timedelta(minutes=1)


@cache
def run(name: str) -> SimResult:
    return simulate(sc.SCENARIOS[name]())


def failures(result: SimResult) -> list[str]:
    return [failure for check in result.scenario.checks for failure in check(result)]


def edit(
    result: SimResult,
    start: timedelta,
    end: timedelta,
    room: str | None = None,
    **changes: object,
) -> SimResult:
    """Change sample fields (or one room's fields) in [start, end)."""
    begin, stop = result.scenario.start + start, result.scenario.start + end

    def changed(sample: Sample) -> Sample:
        if not begin <= sample.time < stop:
            return sample
        if room is None:
            return replace(sample, **changes)  # type: ignore[arg-type] # fields by name
        rooms = tuple(
            replace(r, **changes) if r.name == room else r  # type: ignore[arg-type] # fields by name
            for r in sample.rooms
        )
        return replace(sample, rooms=rooms)

    return replace(result, samples=tuple(changed(sample) for sample in result.samples))


def everywhere(result: SimResult, room: str | None = None, **changes: object) -> SimResult:
    return edit(result, timedelta(0), result.scenario.duration, room, **changes)


def at(offset: timedelta) -> tuple[timedelta, timedelta]:
    return offset, offset + MINUTE


def test_warm_up_late() -> None:
    cold = everywhere(run("morning_warmup"), "bedroom", temp_c=10.0)
    assert failures(cold) == ["bedroom not warm within 120 min"]


def test_window_checks() -> None:
    window = (sc.WINDOW_AT, sc.WINDOW_AT + sc.WINDOW_FOR)
    wrong = edit(run("window_open"), *window, "living_room", status=RoomStatus.SERVED, load_m2k=2.0)
    recovered_at = sc.WINDOW_AT + sc.WINDOW_FOR + timedelta(hours=2)
    wrong = edit(wrong, *at(recovered_at), "living_room", temp_c=15.0)
    found = failures(wrong)
    assert any("served with the window open" in f for f in found)
    assert any("adds load with the window open" in f for f in found)
    assert any("still" in f for f in found)


def test_status_during_fault() -> None:
    wrong = edit(
        run("wall_sensor_dead"),
        *at(sc.FAULT_AT + MINUTE),
        "bedroom",
        status=RoomStatus.SERVED,
        load_m2k=1.0,
    )
    assert len(failures(wrong)) == 2


def test_blocked_checks() -> None:
    never = everywhere(run("valve_never_opens"), "kitchen", status=RoomStatus.SERVED)
    assert failures(never)[0].startswith("kitchen never blocked")
    blocked = everywhere(run("valve_never_opens"), "kitchen", load_m2k=1.0)
    assert "blocked but adds load" in failures(blocked)[0]
    result = run("valve_never_opens")
    first_blocked = next(
        s.time
        for s in result.samples
        if next(r for r in s.rooms if r.name == "kitchen").status is RoomStatus.BLOCKED
    )
    early = first_blocked - result.scenario.start - 5 * MINUTE
    soon = edit(
        result, early, early + 5 * MINUTE, "kitchen", status=RoomStatus.BLOCKED, load_m2k=0.0
    )
    assert "expected 16" in failures(soon)[0]


def test_frozen_checks() -> None:
    never = everywhere(run("wall_sensor_frozen"), "living_room", status=RoomStatus.SERVED)
    assert failures(never) == ["living_room never excluded while frozen"]
    early = edit(
        run("wall_sensor_frozen"),
        *at(sc.FAULT_AT + 5 * MINUTE),
        "living_room",
        status=RoomStatus.WALL_INVALID,
    )
    assert failures(early) == ["living_room excluded 5 min after freezing"]


def test_z2m_checks() -> None:
    result = run("z2m_down")
    off_before = edit(result, *at(sc.Z2M_AT - MINUTE), heating=False)
    assert failures(off_before)[0].startswith("scenario expects")
    window = (sc.Z2M_AT, sc.Z2M_AT + sc.Z2M_FOR)
    assert "without valve data" in failures(edit(result, *window, heating=True))[0]
    wrong_reason = edit(result, *window, reasons=(ReasonCode.IDLE,))
    assert failures(wrong_reason)[0].startswith("reason idle")
    trusted = edit(result, *window, "bedroom", status=RoomStatus.SERVED)
    assert failures(trusted)[0].startswith("a room not fail-closed")


def test_hung_checks() -> None:
    result = run("integration_hung")
    off_before = edit(result, *at(sc.OUTAGE_AT - MINUTE), heating=False)
    assert failures(off_before)[0].startswith("scenario expects")
    hour = (sc.OUTAGE_AT, sc.OUTAGE_AT + timedelta(hours=1))
    assert failures(edit(result, *hour, heating=True)) == ["the board never switched off"]
    early = edit(result, *at(sc.OUTAGE_AT), heating=False, failsafe=FailsafeCause.HEARTBEAT)
    assert failures(early)[0].startswith("board off 1 min")


def test_restart_checks() -> None:
    result = run("ha_restart")
    off_before = edit(result, *at(sc.OUTAGE_AT - MINUTE), heating=False)
    assert failures(off_before)[0].startswith("scenario expects")
    stopped = edit(result, *at(sc.OUTAGE_AT + MINUTE), heating=False)
    assert failures(stopped) == ["the boiler stopped across the restart"]
    failsafe = edit(result, *at(timedelta(0)), failsafe=FailsafeCause.API)
    assert failures(failsafe) == ["the board failsafe fired"]
    disagreed = edit(result, *at(sc.OUTAGE_AT + 2 * MINUTE), reasons=(ReasonCode.EXTERNAL_STOP,))
    assert failures(disagreed) == ["the restored state disagreed with the boiler"]


def test_pause_checks() -> None:
    result = run("pause")
    wrong = edit(result, *at(sc.PAUSE_AT - MINUTE), heating=False)
    wrong = edit(wrong, *at(sc.PAUSE_AT + MINUTE), heating=True)
    end = sc.PAUSE_AT + sc.PAUSE_FOR
    wrong = edit(wrong, end, end + timedelta(minutes=15), heating=False)
    assert len(failures(wrong)) == 3


def test_lockout_checks() -> None:
    result = run("clear_lockout")
    assert failures(everywhere(result, reasons=(ReasonCode.STOP_LOAD,))) == ["no max-run stop"]
    clear = sc.CLEAR_AT
    no_restart = edit(result, clear, result.scenario.duration, heating=False)
    assert failures(no_restart) == ["no start after clearing the lockout"]
    early = edit(result, clear - 10 * MINUTE, clear - 9 * MINUTE, heating=True)
    assert "before the lockout was cleared" in failures(early)[0]
    late = edit(result, clear, clear + 5 * MINUTE, heating=False)
    assert failures(late) == ["start 5 min after clearing the lockout"]
