"""
The guard: hard limits applied after the core (spec 9). It always has the last word.

It can only make the decision safer: veto a start, stop a run, clamp the output. It never
starts the boiler and never keeps it on against the core.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .reasons import Reason, ReasonCode, clock, minutes

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime, timedelta

    from .config import GuardConfig


@dataclass(frozen=True, slots=True)
class GuardInput:
    """The core's wish plus the history and inputs the limits need."""

    now: datetime
    heat: bool
    modulation_pct: int
    on_since: datetime | None
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
    """The command and the guard state. `reason` is set when the guard overrode the core."""

    heat: bool
    modulation_pct: int
    reason: Reason | None
    lockout_until: datetime | None
    no_open_valve_since: datetime | None


def recent_starts(
    starts: Iterable[datetime], now: datetime, window: timedelta
) -> tuple[datetime, ...]:
    """Return the starts inside the rolling window ending now."""
    return tuple(start for start in starts if now - start < window)


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
    ran = guard.now - guard.on_since
    reason: Reason | None
    if ran >= config.max_run:
        lockout = guard.now + config.lockout
        reason = Reason(
            ReasonCode.MAX_RUN,
            f"stopped: ran {minutes(ran)} min, locked out until {clock(lockout)}",
        )
    else:
        reason = _running_stop(guard, lockout, no_open_since, config)
    return _result(
        guard,
        config,
        reason=reason,
        lockout=lockout,
        no_open_since=no_open_since if reason is None else None,
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
    return GuardResult(
        heat=heat,
        modulation_pct=pct if heat else config.idle_pct,
        reason=reason,
        lockout_until=lockout,
        no_open_valve_since=no_open_since,
    )
