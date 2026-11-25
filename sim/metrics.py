"""
What a run means: boiler runs, the spec 9 guard limits and the spec 13 success criteria.

Everything is measured on the physical boiler (the board's switch), not on what the engine
believes, so a disagreement between the two shows up as a violation.
"""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from custom_components.heating_controller.engine.demand import RoomStatus
from custom_components.heating_controller.engine.reasons import ReasonCode

from .loop import Button, Press

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .loop import Sample, SimResult

MINUTE = timedelta(minutes=1)
END = "end"
FAILSAFE_PREFIX = "failsafe_"

SAFETY_STOPS = frozenset(
    {
        ReasonCode.PAUSED,
        ReasonCode.LOCKOUT,
        ReasonCode.MAX_RUN,
        ReasonCode.NO_PROGRESS,
        ReasonCode.NO_OPEN_VALVE,
        ReasonCode.ALL_ROOMS_EXCLUDED,
        ReasonCode.BOILER_UNAVAILABLE,
    }
)
LOCKOUT_STOPS = frozenset({ReasonCode.MAX_RUN, ReasonCode.NO_PROGRESS})


@dataclass(frozen=True, slots=True)
class Run:
    """One physical burner run: first minute on, first minute off (or the end)."""

    start: datetime
    end: datetime
    stop: str

    @property
    def duration(self) -> timedelta:
        """How long the boiler was on."""
        return self.end - self.start

    @property
    def guard_stop(self) -> bool:
        """Stopped by a safety rule (guard, fail-closed, board failsafe), not on load."""
        return self.stop in SAFETY_STOPS or self.stop.startswith(FAILSAFE_PREFIX)


def find_runs(samples: Sequence[Sample]) -> list[Run]:
    """Split the record into runs with the cause of each stop."""
    runs: list[Run] = []
    start: datetime | None = None
    for sample in samples:
        if sample.heating and start is None:
            start = sample.time
        elif not sample.heating and start is not None:
            runs.append(Run(start, sample.time, _stop_cause(sample)))
            start = None
    if start is not None:
        runs.append(Run(start, samples[-1].time + MINUTE, END))
    return runs


def _stop_cause(sample: Sample) -> str:
    if sample.failsafe is not None:
        return FAILSAFE_PREFIX + sample.failsafe
    if sample.reasons:
        return sample.reasons[-1]
    return "unknown"


@dataclass(frozen=True, slots=True)
class Metrics:
    """The numbers a scenario is judged on."""

    starts: int
    max_starts_per_hour: int
    run_minutes: int
    longest_run_min: float
    shortest_off_min: float | None
    short_runs: int
    guard_stops: dict[str, int]
    median_error_k: dict[str, float]
    overheat_min: dict[str, float]
    distinct_pct: int
    mean_pct_on: float | None
    blocked_episodes: int

    @property
    def worst_median_error_k(self) -> float | None:
        """The largest per-room median error, None when no room had eligible minutes."""
        return max(self.median_error_k.values(), default=None)

    @property
    def worst_overheat_min(self) -> float:
        """The longest stretch any room spent over target by more than the limit."""
        return max(self.overheat_min.values(), default=0.0)


@dataclass(frozen=True, slots=True)
class Report:
    """A scenario's metrics and every failed check."""

    name: str
    metrics: Metrics
    failures: tuple[str, ...] = field(default=())

    @property
    def passed(self) -> bool:
        """No check failed."""
        return not self.failures


def max_starts_in_window(starts: Sequence[datetime], window: timedelta) -> int:
    """Return the most starts inside any rolling window."""
    best = 0
    for index, start in enumerate(starts):
        count = sum(1 for other in starts[index:] if other - start < window)
        best = max(best, count)
    return best


def _eligible_minutes(result: SimResult) -> dict[str, list[Sample]]:
    """Per room, the minutes that count for the error and overheat criteria."""
    scenario = result.scenario
    settle_until = scenario.start + scenario.settle
    after_change = scenario.criteria.settle_after_change
    disturbed: dict[str, datetime] = {}
    previous_target: dict[str, float] = {}
    eligible: dict[str, list[Sample]] = {}
    for sample in result.samples:
        for room in sample.rooms:
            target_moved = previous_target.get(room.name, room.target_c) != room.target_c
            previous_target[room.name] = room.target_c
            if target_moved or room.window_open or room.faulted or sample.paused:
                disturbed[room.name] = sample.time
                continue
            since = disturbed.get(room.name)
            if sample.time < settle_until or (
                since is not None and sample.time - since < after_change
            ):
                continue
            eligible.setdefault(room.name, []).append(sample)
    return eligible


def _room_errors(result: SimResult) -> tuple[dict[str, float], dict[str, float]]:
    criteria = result.scenario.criteria
    medians: dict[str, float] = {}
    overheat: dict[str, float] = {}
    for name, samples in _eligible_minutes(result).items():
        rooms = [room for sample in samples for room in sample.rooms if room.name == name]
        medians[name] = statistics.median(abs(room.target_c - room.temp_c) for room in rooms)
        longest = current = 0
        last: datetime | None = None
        for sample, room in zip(samples, rooms, strict=True):
            over = room.temp_c > room.target_c + criteria.overheat_k
            contiguous = last is not None and sample.time - last == MINUTE
            current = (current + 1 if contiguous else 1) if over else 0
            longest = max(longest, current)
            last = sample.time
        overheat[name] = float(longest)
    return medians, overheat


def _blocked_episodes(samples: Sequence[Sample]) -> int:
    episodes = 0
    blocked: set[str] = set()
    for sample in samples:
        if not sample.evaluated:
            continue
        now = {room.name for room in sample.rooms if room.status is RoomStatus.BLOCKED}
        episodes += len(now - blocked)
        blocked = now
    return episodes


def measure(result: SimResult) -> Metrics:
    """Compute the metrics of a run."""
    samples = result.samples
    runs = find_runs(samples)
    criteria = result.scenario.criteria
    guard_stops: dict[str, int] = {}
    for run in runs:
        if run.guard_stop:
            guard_stops[run.stop] = guard_stops.get(run.stop, 0) + 1
    offs = [(later.start - earlier.end) / MINUTE for earlier, later in itertools.pairwise(runs)]
    on_pcts = [sample.power_pct for sample in samples if sample.heating]
    medians, overheat = _room_errors(result)
    return Metrics(
        starts=len(runs),
        max_starts_per_hour=max_starts_in_window([run.start for run in runs], timedelta(hours=1)),
        run_minutes=len(on_pcts),
        longest_run_min=max((run.duration / MINUTE for run in runs), default=0.0),
        shortest_off_min=min(offs, default=None),
        short_runs=sum(
            1
            for run in runs
            if not run.guard_stop and run.stop != END and run.duration < criteria.min_run
        ),
        guard_stops=guard_stops,
        median_error_k=medians,
        overheat_min=overheat,
        distinct_pct=len(set(on_pcts)),
        mean_pct_on=statistics.fmean(on_pcts) if on_pcts else None,
        blocked_episodes=_blocked_episodes(samples),
    )


def guard_limit_failures(result: SimResult) -> list[str]:  # noqa: PLR0912 - one branch per limit
    """Check every spec 9 limit on the physical boiler."""
    guard = result.scenario.config.guard
    samples = result.samples
    runs = find_runs(samples)
    failures: list[str] = []
    starts = [run.start for run in runs]
    if (count := max_starts_in_window(starts, guard.start_window)) > guard.max_starts:
        failures.append(f"{count} starts in one hour (limit {guard.max_starts})")
    for earlier, later in itertools.pairwise(runs):
        if later.start - earlier.end < guard.min_off:
            failures.append(
                f"restart at {later.start:%H:%M} only "
                f"{(later.start - earlier.end) / MINUTE:.0f} min after a stop"
            )
    clears = [
        result.scenario.start + event.at
        for event in result.scenario.events
        if isinstance(event, Press) and event.button is Button.CLEAR_LOCKOUT
    ]
    for index, run in enumerate(runs):
        following = runs[index + 1] if index + 1 < len(runs) else None
        if run.duration > guard.max_run:
            failures.append(f"run from {run.start:%H:%M} lasted {run.duration / MINUTE:.0f} min")
        if run.stop in LOCKOUT_STOPS and following is not None:
            cleared = any(run.end <= clear <= following.start for clear in clears)
            if following.start - run.end < guard.lockout and not cleared:
                failures.append(f"start at {following.start:%H:%M} inside a {run.stop} lockout")
    by_time = {sample.time: sample for sample in samples}
    failures.extend(progress_failures(result, runs, by_time))
    for run in runs:
        sample = by_time[run.start]
        if sample.paused:
            failures.append(f"start at {run.start:%H:%M} while paused")
        if not sample.any_valve_open:
            failures.append(f"start at {run.start:%H:%M} without an open valve")
    for sample in samples:
        if sample.heating and not guard.min_pct <= sample.power_pct <= guard.max_pct:
            failures.append(f"output {sample.power_pct} % at {sample.time:%H:%M}")
            break
        if sample.evaluated and not sample.heating and sample.power_pct != guard.idle_pct:
            failures.append(f"idle output {sample.power_pct} % at {sample.time:%H:%M}")
            break
        if sample.evaluated and sample.paused and sample.heating:
            failures.append(f"heating while paused at {sample.time:%H:%M}")
            break
    return failures


def progress_failures(
    result: SimResult, runs: Sequence[Run], by_time: dict[datetime, Sample]
) -> list[str]:
    """
    Find a run that went on past the progress check although its load had not fallen.

    Judged only where the record has an evaluation exactly one window earlier (inside the
    same run, as the check starts later than one window); that is the sample the guard
    compares with, rounded as the guard stores it.
    """
    guard = result.scenario.config.guard
    for run in runs:
        moment = run.start + guard.progress_check_after
        while moment < run.end:
            sample, earlier = by_time[moment], by_time.get(moment - guard.progress_window)
            moment += MINUTE
            if not sample.evaluated or earlier is None or not earlier.evaluated:
                continue
            reference = round(earlier.load_m2k, guard.load_sample_decimals)
            if sample.load_m2k >= reference - guard.progress_margin_m2k:
                window = guard.progress_window / MINUTE
                return [
                    (
                        f"run from {run.start:%H:%M} went on at {sample.time:%H:%M}: load "
                        f"{reference:.1f} → {sample.load_m2k:.1f} m²·K in {window:.0f} min"
                    )
                ]
    return []


def criteria_failures(result: SimResult, metrics: Metrics) -> list[str]:
    """Check the spec 13 criteria the scenario asks for."""
    criteria = result.scenario.criteria
    failures: list[str] = []
    if metrics.short_runs:
        failures.append(f"{metrics.short_runs} run(s) shorter than {criteria.min_run} on load")
    limit = criteria.max_median_error_k
    if limit is not None:
        for name, error in metrics.median_error_k.items():
            checked = criteria.error_rooms is None or name in criteria.error_rooms
            if checked and error > limit:
                failures.append(f"{name}: median error {error:.2f} K > {limit} K")
    if criteria.check_overheat:
        for name, minutes in metrics.overheat_min.items():
            checked = criteria.overheat_rooms is None or name in criteria.overheat_rooms
            if checked and minutes > criteria.overheat_for / MINUTE:
                failures.append(
                    f"{name}: {minutes:.0f} min more than {criteria.overheat_k} K over target"
                )
    enough_running = metrics.run_minutes >= 120  # noqa: PLR2004 - two hours of running
    if (
        criteria.min_distinct_pct is not None
        and enough_running
        and metrics.distinct_pct < criteria.min_distinct_pct
    ):
        failures.append(f"output took only {metrics.distinct_pct} values")
    failures.extend(
        f"{code} lockout in a normal run"
        for code in sorted(LOCKOUT_STOPS - criteria.allowed_lockouts)
        if code in metrics.guard_stops
    )
    return failures


def assess(result: SimResult) -> Report:
    """Measure a run and apply the guard limits, the criteria and the scenario's own checks."""
    metrics = measure(result)
    failures = guard_limit_failures(result) + criteria_failures(result, metrics)
    for check in result.scenario.checks:
        failures.extend(check(result))
    return Report(result.scenario.name, metrics, tuple(failures))
