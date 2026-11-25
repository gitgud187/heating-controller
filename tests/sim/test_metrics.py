from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from custom_components.heating_controller.engine.demand import RoomStatus
from custom_components.heating_controller.engine.reasons import ReasonCode
from sim.devices import FailsafeCause
from sim.loop import Button, Criteria, Press, RoomSample, Sample, Scenario, SimResult
from sim.metrics import (
    assess,
    criteria_failures,
    find_runs,
    guard_limit_failures,
    max_starts_in_window,
    measure,
)
from sim.scenarios import cold_night

MINUTE = timedelta(minutes=1)
BASE = replace(
    cold_night(), settle=timedelta(0), criteria=Criteria(settle_after_change=timedelta(0))
)


def room(temp_c: float = 21.0, target_c: float = 21.0, **changes: object) -> RoomSample:
    sample = RoomSample(
        name="room",
        temp_c=temp_c,
        wall_c=temp_c,
        target_c=target_c,
        opening=0.5,
        window_open=False,
        status=RoomStatus.SERVED,
        load_m2k=1.0,
        faulted=False,
    )
    return replace(sample, **changes)  # type: ignore[arg-type] # test builder, fields by name


def sample(minute: int, *, heating: bool, **changes: object) -> Sample:
    base = Sample(
        time=BASE.start + minute * MINUTE,
        outdoor_c=0.0,
        heating=heating,
        power_pct=50 if heating else 30,
        evaluated=True,
        reasons=(ReasonCode.RUNNING_LOAD if heating else ReasonCode.STOP_LOAD,),
        any_valve_open=True,
        load_m2k=1.0,
        error_k=0.0,
        base_pct=50.0,
        p_pct=0.0,
        integral_pct=0.0,
        paused=False,
        lockout_until=None,
        failsafe=None,
        rooms=(room(),),
    )
    return replace(base, **changes)  # type: ignore[arg-type] # test builder, fields by name


def result(pattern: str, scenario: Scenario = BASE, **changes: object) -> SimResult:
    """One sample per character: '#' on, '.' off."""
    samples = tuple(
        sample(minute, heating=char == "#", **changes) for minute, char in enumerate(pattern)
    )
    return SimResult(scenario=scenario, samples=samples, restored_states=len(samples))


def with_sample(res: SimResult, minute: int, **changes: object) -> SimResult:
    samples = list(res.samples)
    samples[minute] = replace(samples[minute], **changes)  # type: ignore[arg-type] # test builder
    return replace(res, samples=tuple(samples))


def test_find_runs_with_stop_causes() -> None:
    res = with_sample(result("..###..##"), 5, reasons=(ReasonCode.NO_OPEN_VALVE,))
    runs = find_runs(res.samples)
    assert [(r.duration / MINUTE, r.stop, r.guard_stop) for r in runs] == [
        (3, ReasonCode.NO_OPEN_VALVE, True),
        (2, "end", False),
    ]


def test_failsafe_and_unknown_stop_causes() -> None:
    res = with_sample(result("##.##."), 2, failsafe=FailsafeCause.HEARTBEAT)
    res = with_sample(res, 5, reasons=(), evaluated=False)
    assert [r.stop for r in find_runs(res.samples)] == ["failsafe_heartbeat", "unknown"]


def test_max_starts_in_window() -> None:
    t = BASE.start
    starts = [t, t + 10 * MINUTE, t + 59 * MINUTE, t + 61 * MINUTE]
    assert max_starts_in_window(starts, timedelta(hours=1)) == 3
    assert max_starts_in_window([], timedelta(hours=1)) == 0


def test_clean_run_has_no_failures() -> None:
    res = result("." * 10 + "#" * 30 + "." * 10)
    assert guard_limit_failures(res) == []
    metrics = measure(res)
    assert metrics.starts == 1
    assert metrics.run_minutes == 30
    assert metrics.shortest_off_min is None
    assert criteria_failures(res, metrics) == []
    assert assess(res).passed


def test_too_many_starts_and_short_off() -> None:
    res = result(("#" * 10 + "." * 5) * 4)
    failures = guard_limit_failures(res)
    assert any("starts in one hour" in f for f in failures)
    assert any("min after a stop" in f for f in failures)


def test_run_too_long() -> None:
    res = result("#" * 182 + ".")
    assert any("lasted 182 min" in f for f in guard_limit_failures(res))


def test_start_inside_lockout_unless_cleared() -> None:
    res = with_sample(result("##" + "." * 20 + "##"), 2, reasons=(ReasonCode.MAX_RUN,))
    assert any("inside a max-run lockout" in f for f in guard_limit_failures(res))
    cleared = replace(BASE, events=(Press(Button.CLEAR_LOCKOUT, 10 * MINUTE),))
    res = replace(res, scenario=cleared)
    assert not any("lockout" in f for f in guard_limit_failures(res))


def test_start_while_paused_or_without_open_valve() -> None:
    res = with_sample(result(".##."), 1, paused=True, any_valve_open=False)
    failures = guard_limit_failures(res)
    assert any("while paused" in f for f in failures)
    assert any("without an open valve" in f for f in failures)
    assert any("heating while paused" in f for f in failures)


def test_output_limits() -> None:
    assert any("output 20 %" in f for f in guard_limit_failures(result("#", power_pct=20)))
    assert any("idle output" in f for f in guard_limit_failures(result(".", power_pct=50)))


def test_short_run_and_max_run_criteria() -> None:
    res = with_sample(result("#####." + "#" * 200), 5, reasons=(ReasonCode.STOP_LOAD,))
    res = with_sample(res, 205, reasons=(ReasonCode.MAX_RUN,))
    res = replace(
        res, samples=(*res.samples, sample(206, heating=False, reasons=(ReasonCode.MAX_RUN,)))
    )
    failures = criteria_failures(res, measure(res))
    assert any("shorter than" in f for f in failures)
    assert "max-run lockout in a normal run" in failures
    allowed = replace(res, scenario=replace(BASE, criteria=Criteria(allow_max_run=True)))
    assert "max-run lockout in a normal run" not in criteria_failures(allowed, measure(allowed))


def test_error_overheat_and_output_levels() -> None:
    res = result("#" * 150, rooms=(room(temp_c=22.0),))
    metrics = measure(res)
    assert metrics.median_error_k == {"room": 1.0}
    assert metrics.worst_median_error_k == 1.0
    assert metrics.overheat_min == {"room": 150.0}
    assert metrics.distinct_pct == 1
    failures = criteria_failures(res, metrics)
    assert any("median error" in f for f in failures)
    assert any("over target" in f for f in failures)
    assert any("only 1 values" in f for f in failures)
    lenient = Criteria(
        max_median_error_k=None,
        check_overheat=False,
        min_distinct_pct=None,
        settle_after_change=timedelta(0),
    )
    assert criteria_failures(replace(res, scenario=replace(BASE, criteria=lenient)), metrics) == []
    other_rooms = Criteria(error_rooms=("x",), overheat_rooms=("x",), min_distinct_pct=None)
    assert (
        criteria_failures(replace(res, scenario=replace(BASE, criteria=other_rooms)), metrics) == []
    )


def test_disturbed_minutes_do_not_count() -> None:
    res = result("." * 30, rooms=(room(temp_c=25.0, window_open=True),))
    assert measure(res).median_error_k == {}
    assert measure(res).worst_median_error_k is None
    settling = replace(BASE, criteria=Criteria(settle_after_change=timedelta(minutes=10)))
    moved = result("." * 30, scenario=settling)
    moved = with_sample(moved, 5, rooms=(room(target_c=20.0),))
    assert measure(moved).median_error_k == {"room": 0.0}


def test_blocked_episodes_count_new_blocks() -> None:
    blocked = (room(status=RoomStatus.BLOCKED),)
    res = result("....")
    res = with_sample(res, 1, rooms=blocked)
    res = with_sample(res, 2, rooms=blocked, evaluated=False)
    res = with_sample(res, 3, rooms=blocked)
    assert measure(res).blocked_episodes == 1


def test_custom_checks_are_reported() -> None:
    scenario = replace(BASE, checks=(lambda _result: ["custom"],))
    assert assess(result("..", scenario=scenario)).failures == ("custom",)
