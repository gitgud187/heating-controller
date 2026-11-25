from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from custom_components.heating_controller.engine.config import GuardConfig
from custom_components.heating_controller.engine.guard import (
    GuardInput,
    GuardResult,
    ProgressSample,
    apply_guard,
    progress_reference,
    recent_starts,
    record_progress,
    stuck_rooms,
)
from custom_components.heating_controller.engine.reasons import ReasonCode

from .factories import NOW

CONFIG = GuardConfig()


def ago(minutes: float) -> datetime:
    return NOW - timedelta(minutes=minutes)


def sample(minutes: float, **gaps: float) -> ProgressSample:
    return ProgressSample(ago(minutes), gaps)


def trend(
    gap_now: float, closing_per_hour: float, span: int = 70, room: str = "bedroom"
) -> tuple[ProgressSample, ...]:
    """One sample a minute from `span` minutes ago to a minute ago, one room on a straight line."""
    return tuple(
        sample(back, **{room: round(gap_now + closing_per_hour * back / 60, 2)})
        for back in range(span, 0, -1)
    )


START = GuardInput(
    now=NOW,
    heat=True,
    modulation_pct=50,
    gaps_k={"bedroom": 1.0},
    satisfied=frozenset(),
    on_since=None,
    progress=(),
    last_off=ago(60),
    starts=(),
    lockout_until=None,
    no_open_valve_since=None,
    any_valve_open=True,
    snapshot_complete=True,
    snapshot_age=timedelta(seconds=1),
    paused=False,
    clear_lockout=False,
)
RUNNING = replace(START, on_since=ago(30), starts=(ago(30),))


def guard(base: GuardInput, **changes: object) -> GuardResult:
    return apply_guard(replace(base, **changes), CONFIG)  # type: ignore[arg-type]  # test helper forwards typed fields


def test_start_passes_when_nothing_blocks() -> None:
    result = guard(START)
    assert result.heat
    assert result.reason is None
    assert result.modulation_pct == 50


def test_core_off_stays_off_with_idle_output() -> None:
    result = guard(RUNNING, heat=False, modulation_pct=80, no_open_valve_since=ago(1))
    assert not result.heat
    assert result.reason is None
    assert result.modulation_pct == CONFIG.idle_pct
    assert result.no_open_valve_since is None


@pytest.mark.parametrize(("requested", "sent"), [(10, 30), (30, 30), (100, 100), (120, 100)])
def test_output_clamped_while_on(requested: int, sent: int) -> None:
    assert guard(RUNNING, modulation_pct=requested).modulation_pct == sent


@pytest.mark.parametrize(
    ("changes", "code", "message"),
    [
        ({"lockout_until": NOW + timedelta(minutes=5)}, ReasonCode.LOCKOUT, "locked out until"),
        ({"paused": True}, ReasonCode.PAUSED, "waiting: paused"),
        ({"snapshot_complete": False}, ReasonCode.SNAPSHOT_INCOMPLETE, "inputs incomplete"),
        (
            {"snapshot_age": timedelta(minutes=2, seconds=1)},
            ReasonCode.SNAPSHOT_OLD,
            "inputs 3 min old",
        ),
        ({"last_off": ago(5)}, ReasonCode.MIN_OFF, "minimum off 3 min left"),
        (
            {"starts": (ago(50), ago(30), ago(20))},
            ReasonCode.START_LIMIT,
            "3 starts in the last hour, next at 12:10",
        ),
        ({"any_valve_open": False}, ReasonCode.NO_OPEN_VALVE, "no valve is open"),
    ],
)
def test_start_vetoes(changes: dict[str, object], code: ReasonCode, message: str) -> None:
    result = guard(START, **changes)
    assert not result.heat
    assert result.reason is not None
    assert result.reason.code is code
    assert message in result.reason.message
    assert result.modulation_pct == CONFIG.idle_pct


def test_start_allowed_at_limits() -> None:
    result = guard(
        START,
        last_off=ago(8),
        starts=(ago(60), ago(30), ago(20)),
        snapshot_age=timedelta(minutes=2),
        lockout_until=NOW,
    )
    assert result.heat
    assert result.lockout_until is None


def test_start_without_history() -> None:
    assert guard(START, last_off=None).heat


def test_clear_lockout_ends_lockout() -> None:
    result = guard(START, lockout_until=NOW + timedelta(minutes=30), clear_lockout=True)
    assert result.heat
    assert result.lockout_until is None


def test_lockout_survives_while_core_is_off() -> None:
    until = NOW + timedelta(minutes=30)
    assert guard(START, heat=False, lockout_until=until).lockout_until == until


def test_running_continues_when_nothing_blocks() -> None:
    result = guard(RUNNING, snapshot_complete=False, last_off=ago(1))
    assert result.heat
    assert result.reason is None


def test_max_run_stops_and_locks_out_even_with_progress() -> None:
    result = guard(RUNNING, on_since=ago(360), progress=trend(1.0, 0.5))
    assert not result.heat
    assert result.reason is not None
    assert result.reason.code is ReasonCode.MAX_RUN
    assert result.reason.message == "stopped: ran 6 h, locked out until 13:00"
    assert result.lockout_until == NOW + timedelta(hours=1)
    assert result.progress == ()


def test_run_just_below_max_continues_with_progress() -> None:
    on_since = NOW - timedelta(hours=6) + timedelta(seconds=1)
    assert guard(RUNNING, on_since=on_since, progress=trend(1.0, 0.5)).heat


def test_progress_keeps_a_long_run_alive() -> None:
    result = guard(RUNNING, on_since=ago(300), progress=trend(1.0, 0.3))
    assert result.heat
    assert result.reason is None
    assert result.lockout_until is None


def test_stall_after_3_h_stops_with_lockout() -> None:
    result = guard(
        RUNNING,
        on_since=ago(192),
        gaps_k={"bedroom": 1.4},
        progress=trend(1.4, 0.0),
    )
    assert not result.heat
    assert result.reason is not None
    assert result.reason.code is ReasonCode.NO_PROGRESS
    assert result.reason.message == (
        "stopped: ran 3 h 12 min, no room closer to target in 60 min (bedroom 1.4 → 1.4 K), "
        "locked out until 13:00"
    )
    assert result.lockout_until == NOW + timedelta(hours=1)
    assert result.modulation_pct == CONFIG.idle_pct
    assert result.progress == ()


def test_stall_before_the_check_is_not_judged() -> None:
    on_since = NOW - timedelta(hours=3) + timedelta(seconds=1)
    assert guard(RUNNING, on_since=on_since, progress=trend(1.0, 0.0)).heat


def test_stall_is_caught_whenever_it_starts() -> None:
    result = guard(RUNNING, on_since=ago(270), gaps_k={"bedroom": 0.9}, progress=trend(0.9, 0.1))
    assert result.reason is not None
    assert result.reason.code is ReasonCode.NO_PROGRESS
    assert result.reason.message.startswith("stopped: ran 4 h 30 min, no room closer")


@pytest.mark.parametrize(("gap", "heat"), [(0.8, True), (0.81, False), (1.2, False)])
def test_progress_margin(gap: float, heat: bool) -> None:  # noqa: FBT001 - parametrized
    history = (sample(60, bedroom=1.0), sample(1, bedroom=0.9))
    result = guard(RUNNING, on_since=ago(200), gaps_k={"bedroom": gap}, progress=history)
    assert result.heat is heat


def test_one_progressing_room_is_enough() -> None:
    history = (sample(60, bedroom=1.0, kitchen=0.5),)
    gaps = {"bedroom": 1.0, "kitchen": 0.2}
    assert guard(RUNNING, on_since=ago(200), gaps_k=gaps, progress=history).heat


def test_a_room_that_reached_its_target_is_progress() -> None:
    history = (sample(60, bedroom=1.0, living=0.4),)
    result = guard(
        RUNNING,
        on_since=ago(200),
        gaps_k={"bedroom": 1.0},
        satisfied=frozenset({"living"}),
        progress=history,
    )
    assert result.heat


def test_a_room_that_asks_again_does_not_count_against_the_run() -> None:
    # The living room was satisfied an hour ago and asks again now; only the bedroom is judged.
    history = (sample(60, bedroom=1.0),)
    gaps = {"bedroom": 0.7, "living": 0.3}
    assert guard(RUNNING, on_since=ago(200), gaps_k=gaps, progress=history).heat


def test_a_room_that_left_service_is_not_progress() -> None:
    # Blocked or excluded now: neither served nor satisfied, so it proves nothing.
    history = (sample(60, bedroom=1.0),)
    result = guard(RUNNING, on_since=ago(200), gaps_k={}, progress=history)
    assert result.reason is not None
    assert "(bedroom 1.0 → - K)" in result.reason.message


def test_no_verdict_when_no_room_was_served_an_hour_ago() -> None:
    assert guard(RUNNING, on_since=ago(200), progress=(sample(60),)).heat


def test_no_verdict_without_a_sample_a_full_window_old() -> None:
    result = guard(RUNNING, on_since=ago(200), progress=trend(1.0, 0.0, span=59))
    assert result.heat
    assert result.progress[-1] == sample(0, bedroom=1.0)
    assert len(result.progress) == 60


def test_restart_gap_compares_with_the_newest_sample_a_window_old() -> None:
    history = (*trend(1.0, 0.0)[:8], *trend(1.0, 0.0, span=5))  # 70..63 and 5..1 min ago
    result = guard(RUNNING, on_since=ago(200), progress=history)
    assert result.reason is not None
    assert result.reason.code is ReasonCode.NO_PROGRESS
    assert "in 63 min (bedroom 1.0 → 1.0 K)" in result.reason.message


def test_long_restart_gap_leaves_no_verdict() -> None:
    restored = (sample(75, bedroom=1.0), *trend(1.0, 0.0, span=5))
    result = guard(RUNNING, on_since=ago(200), progress=restored)
    assert result.heat
    assert result.progress[0] == sample(5, bedroom=1.0)


def test_start_records_the_first_sample() -> None:
    assert guard(START, gaps_k={"bedroom": 1.126}).progress == (sample(0, bedroom=1.13),)
    assert guard(START, paused=True).progress == ()


def test_core_stop_clears_the_history() -> None:
    assert guard(RUNNING, heat=False, progress=trend(1.0, 0.0)).progress == ()


def test_record_progress_samples_once_a_minute_and_trims() -> None:
    recent = (sample(0.5, bedroom=1.0),)
    assert record_progress(recent, NOW, {"bedroom": 0.5}, CONFIG) == recent
    old = (sample(70.5, bedroom=2.0), sample(70, bedroom=1.5), sample(1, bedroom=1.0))
    expected = (*old[1:], sample(0, bedroom=0.5))
    assert record_progress(old, NOW, {"bedroom": 0.5}, CONFIG) == expected


def test_progress_reference() -> None:
    samples = (sample(61, a=3.0), sample(60, a=2.0), sample(59, a=1.0))
    assert progress_reference(samples, NOW, CONFIG) == samples[1]
    assert progress_reference(samples[2:], NOW, CONFIG) is None
    assert progress_reference((sample(70.5, a=3.0),), NOW, CONFIG) is None


def test_stuck_rooms_lists_every_room_served_then() -> None:
    reference = sample(60, bedroom=1.0, kitchen=0.5)
    stuck = stuck_rooms(reference, {"bedroom": 0.9}, frozenset(), CONFIG)
    assert stuck == {"bedroom": (1.0, 0.9), "kitchen": (0.5, None)}


def test_clear_lockout_after_a_stall_allows_a_start() -> None:
    stalled = guard(RUNNING, on_since=ago(200), progress=trend(1.0, 0.0))
    assert stalled.lockout_until is not None
    vetoed = guard(START, last_off=ago(10), lockout_until=stalled.lockout_until)
    assert vetoed.reason is not None
    assert vetoed.reason.code is ReasonCode.LOCKOUT
    cleared = guard(
        START, last_off=ago(10), lockout_until=stalled.lockout_until, clear_lockout=True
    )
    assert cleared.heat
    assert cleared.lockout_until is None


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"paused": True}, ReasonCode.PAUSED),
        ({"lockout_until": NOW + timedelta(minutes=5)}, ReasonCode.LOCKOUT),
        ({"any_valve_open": False, "no_open_valve_since": ago(3)}, ReasonCode.NO_OPEN_VALVE),
    ],
)
def test_running_stops(changes: dict[str, object], code: ReasonCode) -> None:
    result = guard(RUNNING, **changes)
    assert not result.heat
    assert result.reason is not None
    assert result.reason.code is code
    assert result.no_open_valve_since is None


def test_no_open_valve_clock_starts_and_runs() -> None:
    first = guard(RUNNING, any_valve_open=False)
    assert first.heat
    assert first.no_open_valve_since == NOW
    later = guard(RUNNING, any_valve_open=False, no_open_valve_since=ago(2))
    assert later.heat
    assert later.no_open_valve_since == ago(2)


def test_open_valve_resets_no_open_clock() -> None:
    assert guard(RUNNING, no_open_valve_since=ago(2)).no_open_valve_since is None


def test_recent_starts_window() -> None:
    assert recent_starts([ago(61), ago(60), ago(59)], NOW, timedelta(hours=1)) == (ago(59),)


# --- property: the guard only ever makes the decision safer ------------------------------


def _maybe(rng: random.Random, value: datetime) -> datetime | None:
    return value if rng.random() < 0.7 else None


ROOMS = ("bathroom", "kitchen", "bedroom", "living")


def _random_gaps(rng: random.Random) -> dict[str, float]:
    return {room: round(rng.uniform(0, 3), 2) for room in ROOMS if rng.random() < 0.5}


def _random_history(rng: random.Random, on_since: datetime | None) -> tuple[ProgressSample, ...]:
    """A minute-by-minute gap history over the run, sometimes with a restart gap."""
    if on_since is None:
        return ()
    start, slope = _random_gaps(rng), rng.uniform(-1, 1) / 60
    gap = range(rng.randint(1, 80), rng.randint(1, 80)) if rng.random() < 0.3 else range(0)
    span = min(int((NOW - on_since) / timedelta(minutes=1)), rng.randint(0, 80))
    return tuple(
        ProgressSample(
            ago(back),
            {
                room: round(value + slope * back + rng.uniform(-0.1, 0.1), 2)
                for room, value in start.items()
            },
        )
        for back in range(span, 0, -1)
        if back not in gap
    )


def random_input(rng: random.Random) -> GuardInput:
    def minutes_ago(low: float, high: float) -> datetime:
        return NOW - timedelta(minutes=rng.uniform(low, high))

    running = rng.random() < 0.5
    on_since = minutes_ago(0, 420) if running else None
    return GuardInput(
        now=NOW,
        heat=rng.random() < 0.8,
        modulation_pct=rng.randint(-20, 140),
        gaps_k=_random_gaps(rng),
        satisfied=frozenset(room for room in ROOMS if rng.random() < 0.2),
        on_since=on_since,
        progress=_random_history(rng, on_since),
        last_off=_maybe(rng, minutes_ago(0, 30)),
        starts=tuple(minutes_ago(0, 90) for _ in range(rng.randint(0, 5))),
        lockout_until=_maybe(rng, minutes_ago(-60, 30)),
        no_open_valve_since=_maybe(rng, minutes_ago(0, 6)),
        any_valve_open=rng.random() < 0.6,
        snapshot_complete=rng.random() < 0.8,
        snapshot_age=timedelta(minutes=rng.uniform(0, 4)),
        paused=rng.random() < 0.2,
        clear_lockout=rng.random() < 0.2,
    )


def assert_never_less_safe(inp: GuardInput, out: GuardResult) -> None:
    # The guard never turns on what the core wants off.
    if not inp.heat:
        assert not out.heat
    # Output range.
    if out.heat:
        assert CONFIG.min_pct <= out.modulation_pct <= CONFIG.max_pct
    else:
        assert out.modulation_pct == CONFIG.idle_pct
    # A run limit always locks out; the progress history is only kept while the boiler runs.
    if out.reason is not None and out.reason.code in {ReasonCode.MAX_RUN, ReasonCode.NO_PROGRESS}:
        assert out.lockout_until == NOW + CONFIG.lockout
    keep = CONFIG.progress_window + CONFIG.progress_history_slack
    assert all(timedelta(0) <= NOW - kept.at <= keep for kept in out.progress)
    assert bool(out.progress) is out.heat
    # An active lockout means off; a lockout is only shortened by the button or expiry.
    if out.lockout_until is not None and out.lockout_until > NOW:
        assert not out.heat
    if inp.lockout_until is not None and inp.lockout_until > NOW and not inp.clear_lockout:
        assert out.lockout_until is not None
        assert out.lockout_until >= inp.lockout_until
    if not out.heat:
        return
    assert not inp.paused
    if inp.on_since is None:
        assert inp.snapshot_complete
        assert inp.snapshot_age <= CONFIG.snapshot_max_age
        assert inp.any_valve_open
        assert inp.last_off is None or NOW - inp.last_off >= CONFIG.min_off
        assert len(recent_starts(inp.starts, NOW, CONFIG.start_window)) < CONFIG.max_starts
    else:
        ran = NOW - inp.on_since
        assert ran < CONFIG.max_run
        old_enough = [s for s in inp.progress if CONFIG.progress_window <= NOW - s.at <= keep]
        if ran >= CONFIG.progress_check_after and old_enough and old_enough[-1].gaps_k:
            # Independent restatement of the per-room rule: some room served then progressed.
            assert any(
                room in inp.satisfied
                or (
                    inp.gaps_k.get(room) is not None
                    and inp.gaps_k[room] <= before - CONFIG.progress_margin_k
                )
                for room, before in old_enough[-1].gaps_k.items()
            )
        if not inp.any_valve_open and inp.no_open_valve_since is not None:
            assert NOW - inp.no_open_valve_since < CONFIG.no_open_valve_after


@pytest.mark.parametrize("seed", range(20))
def test_guard_never_less_safe_than_core(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - reproducible test data, not cryptography
    for _ in range(250):
        inp = random_input(rng)
        assert_never_less_safe(inp, apply_guard(inp, CONFIG))
