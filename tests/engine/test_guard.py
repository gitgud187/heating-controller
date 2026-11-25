from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from custom_components.heating_controller.engine.config import GuardConfig
from custom_components.heating_controller.engine.guard import (
    GuardInput,
    GuardResult,
    apply_guard,
    recent_starts,
)
from custom_components.heating_controller.engine.reasons import ReasonCode

from .factories import NOW

CONFIG = GuardConfig()


def ago(minutes: float) -> datetime:
    return NOW - timedelta(minutes=minutes)


START = GuardInput(
    now=NOW,
    heat=True,
    modulation_pct=50,
    on_since=None,
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


def test_max_run_stops_and_locks_out() -> None:
    result = guard(RUNNING, on_since=ago(180))
    assert not result.heat
    assert result.reason is not None
    assert result.reason.code is ReasonCode.MAX_RUN
    assert result.reason.message == "stopped: ran 180 min, locked out until 13:00"
    assert result.lockout_until == NOW + timedelta(hours=1)


def test_run_just_below_max_continues() -> None:
    assert guard(RUNNING, on_since=NOW - timedelta(hours=3) + timedelta(seconds=1)).heat


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


def random_input(rng: random.Random) -> GuardInput:
    def minutes_ago(low: float, high: float) -> datetime:
        return NOW - timedelta(minutes=rng.uniform(low, high))

    running = rng.random() < 0.5
    return GuardInput(
        now=NOW,
        heat=rng.random() < 0.8,
        modulation_pct=rng.randint(-20, 140),
        on_since=minutes_ago(0, 240) if running else None,
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
        assert NOW - inp.on_since < CONFIG.max_run
        if not inp.any_valve_open and inp.no_open_valve_since is not None:
            assert NOW - inp.no_open_valve_since < CONFIG.no_open_valve_after


@pytest.mark.parametrize("seed", range(20))
def test_guard_never_less_safe_than_core(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - reproducible test data, not cryptography
    for _ in range(250):
        inp = random_input(rng)
        assert_never_less_safe(inp, apply_guard(inp, CONFIG))
