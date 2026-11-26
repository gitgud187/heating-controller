"""Output request: heating curve plus area-weighted PI on the room error (spec 8)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from .demand import RoomStatus

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from .config import ModulationConfig
    from .demand import RoomAssessment

_HOUR = timedelta(hours=1)
# Rooms whose error drives the output. Satisfied rooms count with their (negative) error, so a
# room above target pulls the output down and the integral can unwind while the boiler runs.
# Blocked, settled and excluded rooms cannot take heat and do not count.
_SIGNED = frozenset({RoomStatus.SERVED, RoomStatus.WAITING_FOR_VALVE, RoomStatus.SATISFIED})


@dataclass(frozen=True, slots=True)
class ModulationInput:
    """
    Inputs of one modulation step.

    `on_since` and `integral_pct` are the previous state, so the integral covers the interval
    since `last_evaluated` with the boiler as it was. `starting` is True when the core turns
    the boiler on now; the first value is then written without the write threshold.
    """

    now: datetime
    rooms: tuple[RoomAssessment, ...]
    outdoor_c: float | None
    integral_pct: float
    last_evaluated: datetime | None
    on_since: datetime | None
    last_pct: int | None
    starting: bool


@dataclass(frozen=True, slots=True)
class ModulationResult:
    """Every term, for the entities, and the value to write."""

    base_pct: float
    outdoor_fallback: bool
    error_k: float
    p_pct: float
    integral_pct: float
    target_pct: int
    output_pct: int


def base_pct(outdoor_c: float, config: ModulationConfig) -> float:
    """Heating curve: linear between the mild and the cold point, clamped to both."""
    slope = (config.cold_base_pct - config.mild_base_pct) / (
        config.mild_outdoor_c - config.cold_outdoor_c
    )
    raw = config.mild_base_pct + (config.mild_outdoor_c - outdoor_c) * slope
    return _clamp(raw, config.mild_base_pct, config.cold_base_pct)


def weighted_error(rooms: Iterable[RoomAssessment], satisfied_floor_k: float) -> float:
    """
    Signed error, area-weighted over served and satisfied rooms; 0 without any.

    Counting only served rooms made the error never negative (a room leaves `served` at error
    0), so the integral could only grow while the boiler ran. A satisfied room counts with its
    error, but no lower than `satisfied_floor_k`: a room far above target (a setback, sun) is
    out of the boiler's reach and must not pull the others' heat away.
    """
    taking_part = [
        (room.area_m2, max(room.error_k, satisfied_floor_k))
        for room in rooms
        if room.status in _SIGNED and room.error_k is not None
    ]
    area = sum(area for area, _ in taking_part)
    if area == 0:
        return 0.0
    return sum(area * error for area, error in taking_part) / area


def update_integral(  # noqa: PLR0913 - every argument is a separate term of the formula
    *,
    integral_pct: float,
    error_k: float,
    proportional_pct: float,
    dt: timedelta,
    running_for: timedelta | None,
    config: ModulationConfig,
    hold: bool = False,
) -> float:
    """
    Advance the integral by one interval.

    Off: decay toward 0. On: frozen for the first minutes of a run, while `hold` (a room is
    waiting for its valve, so its error says nothing about the output yet) and while the
    output sits at a bound, else integrate (the step is capped so a long gap cannot jump to
    the bound). `proportional_pct` is base plus P, to judge saturation.
    """
    if running_for is None:
        return integral_pct * math.exp(-dt / config.decay_time_constant)
    if running_for < config.integral_delay or hold:
        return integral_pct
    output = proportional_pct + integral_pct
    if output <= config.min_pct or output >= config.max_pct:
        return integral_pct
    step = min(dt, config.max_integration_step)
    grown = integral_pct + config.ki_pct_per_k_h * error_k * (step / _HOUR)
    return _clamp(grown, -config.integral_limit_pct, config.integral_limit_pct)


def modulate(inputs: ModulationInput, config: ModulationConfig) -> ModulationResult:
    """Compute the output request and what to write."""
    outdoor_fallback = inputs.outdoor_c is None
    outdoor_c = config.fallback_outdoor_c if inputs.outdoor_c is None else inputs.outdoor_c
    base = base_pct(outdoor_c, config)
    error_k = weighted_error(inputs.rooms, config.satisfied_floor_k)
    p_pct = config.kp_pct_per_k * error_k
    dt = inputs.now - inputs.last_evaluated if inputs.last_evaluated else timedelta(0)
    integral = update_integral(
        integral_pct=inputs.integral_pct,
        error_k=error_k,
        proportional_pct=base + p_pct,
        dt=max(dt, timedelta(0)),
        running_for=None if inputs.on_since is None else inputs.now - inputs.on_since,
        config=config,
        hold=any(room.status is RoomStatus.WAITING_FOR_VALVE for room in inputs.rooms),
    )
    target = _round_half_up(_clamp(base + p_pct + integral, config.min_pct, config.max_pct))
    last = inputs.last_pct
    write = inputs.starting or last is None or abs(target - last) >= config.write_step_pct
    return ModulationResult(
        base_pct=base,
        outdoor_fallback=outdoor_fallback,
        error_k=error_k,
        p_pct=p_pct,
        integral_pct=integral,
        target_pct=target,
        output_pct=target if write or last is None else last,
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _round_half_up(value: float) -> int:
    return math.floor(value + 0.5)
