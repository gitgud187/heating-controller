"""
The guard: hard limits applied after the core (spec 9). It always has the last word.

It can only make the decision safer: veto a start, stop a run, clamp the output. It never
starts the boiler and never keeps it on against the core.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .reasons import Reason, ReasonCode, clock, duration, minutes

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import datetime, timedelta

    from .config import GuardConfig


@dataclass(frozen=True, slots=True)
class ProgressSample:
    """The gap (target minus wall, K) of every served room at one evaluation of a run."""

    at: datetime
    gaps_k: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class GuardInput:
    """
    The core's wish plus the history and inputs the limits need.

    `gaps_k` holds the current gap of every served room; `satisfied` names the rooms that
    have reached their target. `progress` is the sampled history of the current run, oldest
    first (empty while off).
    """

    now: datetime
    heat: bool
    modulation_pct: int
    gaps_k: Mapping[str, float]
    satisfied: frozenset[str]
    on_since: datetime | None
    progress: tuple[ProgressSample, ...]
    last_off: datetime | None
    starts: tuple[datetime, ...]
    lockout_until: datetime | None
    no_open_valve_since: datetime | None
    any_valve_open: bool
    snapshot_complete: bool
    snapshot_age: timedelta
    paused: bool
    clear_lockout: bool


@dataclass(frozen=True, slots=True)
class GuardResult:
    """
    The command and the guard state. `reason` is set when the guard overrode the core.

    `progress` is the history to keep: empty unless the boiler stays or goes on.
    """

    heat: bool
    modulation_pct: int
    reason: Reason | None
    lockout_until: datetime | None
    no_open_valve_since: datetime | None
    progress: tuple[ProgressSample, ...]


def recent_starts(
    starts: Iterable[datetime], now: datetime, window: timedelta
) -> tuple[datetime, ...]:
    """Return the starts inside the rolling window ending now."""
    return tuple(start for start in starts if now - start < window)


def record_progress(
    samples: tuple[ProgressSample, ...],
    now: datetime,
    gaps_k: Mapping[str, float],
    config: GuardConfig,
) -> tuple[ProgressSample, ...]:
    """
    Add the served rooms' gaps to the run's history and drop what the check no longer needs.

    At most one sample per `progress_sample_interval`; samples older than the progress window
    plus the slack are dropped, so the history stays bounded.
    """
    if not samples or now - samples[-1].at >= config.progress_sample_interval:
        rounded = {
            name: round(gap, config.progress_sample_decimals) for name, gap in gaps_k.items()
        }
        samples = (*samples, ProgressSample(now, rounded))
    keep = config.progress_window + config.progress_history_slack
    return tuple(sample for sample in samples if now - sample.at <= keep)


def progress_reference(
    samples: tuple[ProgressSample, ...], now: datetime, config: GuardConfig
) -> ProgressSample | None:
    """
    Return the newest sample at least the progress window old.

    None when there is none, or when it is older than the window plus the slack (a long
    restart gap): the check then waits until the history covers a window again.
    """
    window, keep = config.progress_window, config.progress_window + config.progress_history_slack
    return next((sample for sample in reversed(samples) if window <= now - sample.at <= keep), None)


def stuck_rooms(
    reference: ProgressSample,
    gaps_k: Mapping[str, float],
    satisfied: frozenset[str],
    config: GuardConfig,
) -> dict[str, tuple[float, float | None]] | None:
    """
    Judge progress per room against the reference sample.

    Only rooms served at the reference count. One of them made progress if it is satisfied
    now, or its gap shrank by at least `progress_margin_k`. Returns None when any room made
    progress (or none was served then), else each room's gap then and now (None: no longer
    served, e.g. blocked or excluded).
    """
    if not reference.gaps_k:
        return None
    stuck: dict[str, tuple[float, float | None]] = {}
    for name, before in reference.gaps_k.items():
        if name in satisfied:
            return None
        after = gaps_k.get(name)
        if after is not None and after <= before - config.progress_margin_k:
            return None
        stuck[name] = (before, after)
    return stuck


def apply_guard(guard: GuardInput, config: GuardConfig) -> GuardResult:
    """Apply every limit; return the command that is actually sent."""
    lockout = None if guard.clear_lockout else guard.lockout_until
    if lockout is not None and guard.now >= lockout:
        lockout = None
    if not guard.heat:
        return _result(guard, config, reason=None, lockout=lockout, no_open_since=None)
    if guard.on_since is None:
        veto = _start_veto(guard, lockout, config)
        return _result(guard, config, reason=veto, lockout=lockout, no_open_since=None)

    no_open_since = None if guard.any_valve_open else guard.no_open_valve_since or guard.now
    reason: Reason | None
    limit = _run_limit(guard, guard.now - guard.on_since, config)
    if limit is not None:
        code, what = limit
        lockout = guard.now + config.lockout
        reason = Reason(code, f"stopped: {what}, locked out until {clock(lockout)}")
    else:
        reason = _running_stop(guard, lockout, no_open_since, config)
    return _result(
        guard,
        config,
        reason=reason,
        lockout=lockout,
        no_open_since=no_open_since if reason is None else None,
    )


def _run_limit(
    guard: GuardInput, ran: timedelta, config: GuardConfig
) -> tuple[ReasonCode, str] | None:
    """
    Limit a run's length: absolute at `max_run`, earlier when no served room progresses.

    Without a sample a full window old (a fresh run, or a restart gap) there is no verdict
    on progress; `max_run` still applies.
    """
    if ran >= config.max_run:
        return ReasonCode.MAX_RUN, f"ran {duration(ran)}"
    if ran < config.progress_check_after:
        return None
    reference = progress_reference(guard.progress, guard.now, config)
    if reference is None:
        return None
    stuck = stuck_rooms(reference, guard.gaps_k, guard.satisfied, config)
    if stuck is None:
        return None
    rooms = ", ".join(
        f"{name} {before:.1f} → {'-' if after is None else f'{after:.1f}'} K"
        for name, (before, after) in sorted(stuck.items())
    )
    window = minutes(guard.now - reference.at)
    return (
        ReasonCode.NO_PROGRESS,
        f"ran {duration(ran)}, no room closer to target in {window} min ({rooms})",
    )


def _running_stop(
    guard: GuardInput,
    lockout: datetime | None,
    no_open_since: datetime | None,
    config: GuardConfig,
) -> Reason | None:
    if guard.paused:
        return Reason(ReasonCode.PAUSED, "stopped: paused")
    if lockout is not None:
        return Reason(ReasonCode.LOCKOUT, f"stopped: locked out until {clock(lockout)}")
    if no_open_since is not None and guard.now - no_open_since >= config.no_open_valve_after:
        closed = minutes(guard.now - no_open_since)
        return Reason(ReasonCode.NO_OPEN_VALVE, f"stopped: no open valve for {closed} min")
    return None


def _start_veto(  # noqa: PLR0911 - one return per limit reads best
    guard: GuardInput, lockout: datetime | None, config: GuardConfig
) -> Reason | None:
    if lockout is not None:
        return Reason(ReasonCode.LOCKOUT, f"waiting: locked out until {clock(lockout)}")
    if guard.paused:
        return Reason(ReasonCode.PAUSED, "waiting: paused")
    if not guard.snapshot_complete:
        return Reason(ReasonCode.SNAPSHOT_INCOMPLETE, "waiting: inputs incomplete")
    if guard.snapshot_age > config.snapshot_max_age:
        age = minutes(guard.snapshot_age)
        return Reason(ReasonCode.SNAPSHOT_OLD, f"waiting: inputs {age} min old")
    if guard.last_off is not None and guard.now - guard.last_off < config.min_off:
        left = minutes(config.min_off - (guard.now - guard.last_off))
        return Reason(ReasonCode.MIN_OFF, f"waiting: minimum off {left} min left")
    recent = recent_starts(guard.starts, guard.now, config.start_window)
    if len(recent) >= config.max_starts:
        next_start = min(recent) + config.start_window
        return Reason(
            ReasonCode.START_LIMIT,
            f"waiting: {len(recent)} starts in the last hour, next at {clock(next_start)}",
        )
    if not guard.any_valve_open:
        return Reason(ReasonCode.NO_OPEN_VALVE, "waiting: no valve is open")
    return None


def _result(
    guard: GuardInput,
    config: GuardConfig,
    *,
    reason: Reason | None,
    lockout: datetime | None,
    no_open_since: datetime | None,
) -> GuardResult:
    heat = guard.heat and reason is None
    pct = max(config.min_pct, min(config.max_pct, guard.modulation_pct))
    history = guard.progress if guard.on_since is not None else ()
    return GuardResult(
        heat=heat,
        modulation_pct=pct if heat else config.idle_pct,
        reason=reason,
        lockout_until=lockout,
        no_open_valve_since=no_open_since,
        progress=record_progress(history, guard.now, guard.gaps_k, config) if heat else (),
    )
