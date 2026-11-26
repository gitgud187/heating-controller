"""
Compare modulation variants over every scenario, several seeds and several plants.

    uv run python -m sim.tune                 # full comparison (a few minutes)
    uv run python -m sim.tune --quick         # default plant, one seed

Gains are engine configuration. Variants the engine does not implement (conditional
integration, the earlier error over served rooms only) are swapped into the engine's
modulation module for the duration of their runs only.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING

from custom_components.heating_controller.engine import modulation
from custom_components.heating_controller.engine.demand import RoomStatus
from custom_components.heating_controller.engine.reasons import ReasonCode

from .house import HouseSpec
from .loop import simulate
from .metrics import Report, assess
from .scenarios import SCENARIOS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence

    from custom_components.heating_controller.engine.config import EngineConfig, ModulationConfig
    from custom_components.heating_controller.engine.demand import RoomAssessment

_HOUR = timedelta(hours=1)
# Scenarios that provoke run-limit lockouts on purpose; their errors say nothing about tuning.
LOCKOUT_SCENARIOS = frozenset({"clear_lockout", "undersized_radiator"})
SERVED_STATUSES = frozenset({RoomStatus.SERVED, RoomStatus.WAITING_FOR_VALVE})


def conditional_integral(  # noqa: PLR0913 - same signature as the engine's update_integral
    *,
    integral_pct: float,
    error_k: float,
    proportional_pct: float,
    dt: timedelta,
    running_for: timedelta | None,
    config: ModulationConfig,
    hold: bool = False,
) -> float:
    """Anti-windup by conditional integration: freeze only when the error pushes further out."""
    if running_for is None:
        return integral_pct * math.exp(-dt / config.decay_time_constant)
    if running_for < config.integral_delay or hold:
        return integral_pct
    output = proportional_pct + integral_pct
    if (output >= config.max_pct and error_k > 0) or (output <= config.min_pct and error_k < 0):
        return integral_pct
    step = min(dt, config.max_integration_step)
    grown = integral_pct + config.ki_pct_per_k_h * error_k * (step / _HOUR)
    return max(-config.integral_limit_pct, min(config.integral_limit_pct, grown))


def served_error(rooms: Iterable[RoomAssessment], satisfied_floor_k: float) -> float:
    """Return the earlier error: area-weighted over served rooms only, never negative."""
    del satisfied_floor_k  # same signature as the engine's weighted_error; no satisfied rooms
    served = [r for r in rooms if r.status in SERVED_STATUSES and r.error_k is not None]
    area = sum(room.area_m2 for room in served)
    if area == 0:
        return 0.0
    return sum(room.area_m2 * (room.error_k or 0.0) for room in served) / area


@dataclass(frozen=True, slots=True)
class Variant:
    """A modulation variant: gains plus optional replacements of engine functions."""

    name: str
    kp: float = 15.0
    ki: float = 10.0
    integral: Callable[..., float] | None = None
    error: Callable[[Iterable[RoomAssessment], float], float] | None = None

    def config(self, base: EngineConfig) -> EngineConfig:
        """Return the engine config with this variant's gains."""
        gains = replace(base.modulation, kp_pct_per_k=self.kp, ki_pct_per_k_h=self.ki)
        return replace(base, modulation=gains)


VARIANTS = (
    Variant("spec: Kp 15, Ki 10, signed error, freeze at bounds"),
    Variant("served-only error (before)", error=served_error),
    Variant("conditional integration", integral=conditional_integral),
    Variant("P only (Ki 0)", ki=0.0),
    Variant("Kp 10, Ki 5", kp=10.0, ki=5.0),
    Variant("Kp 15, Ki 5", ki=5.0),
    Variant("Kp 15, Ki 20", ki=20.0),
    Variant("Kp 25, Ki 10", kp=25.0),
    Variant("signed error + conditional", integral=conditional_integral),
)


@dataclass(frozen=True, slots=True)
class Plant:
    """A variation of the default house: radiator size and room time constant factors."""

    name: str
    radiator: float = 1.0
    capacity: float = 1.0

    def apply(self, house: HouseSpec) -> HouseSpec:
        """Scale every room."""
        rooms = tuple(
            replace(
                spec,
                params=replace(
                    spec.params,
                    radiator_w=spec.params.radiator_w * self.radiator,
                    capacity_j_per_k=spec.params.capacity_j_per_k * self.capacity,
                ),
            )
            for spec in house.rooms
        )
        return HouseSpec(rooms, house.couplings)


PLANTS = (
    Plant("default"),
    Plant("radiators -15 %", radiator=0.85),
    Plant("radiators +30 %", radiator=1.3),
    Plant("slow rooms (x1.6)", capacity=1.6),
    Plant("fast rooms (x0.6)", capacity=0.6),
)


@contextmanager
def _swapped(name: str, replacement: object | None) -> Iterator[None]:
    if replacement is None:
        yield
        return
    original = getattr(modulation, name)
    setattr(modulation, name, replacement)
    try:
        yield
    finally:
        setattr(modulation, name, original)


def run_variant(
    variant: Variant, plants: Sequence[Plant], seeds: Sequence[int]
) -> list[tuple[str, Report]]:
    """Every scenario for every plant and seed; returns (scenario name, report) pairs."""
    reports = []
    with ExitStack() as stack:
        stack.enter_context(_swapped("update_integral", variant.integral))
        stack.enter_context(_swapped("weighted_error", variant.error))
        for plant in plants:
            for seed in seeds:
                for name, factory in SCENARIOS.items():
                    scenario = factory()
                    scenario = replace(
                        scenario,
                        config=variant.config(scenario.config),
                        house=plant.apply(scenario.house),
                        seed=seed,
                    )
                    reports.append((name, assess(simulate(scenario))))
    return reports


def _mean(values: Iterable[float], digits: int) -> str:
    items = list(values)
    return f"{statistics.fmean(items):.{digits}f}" if items else "-"


def summarise(variant: Variant, reports: Sequence[tuple[str, Report]]) -> str:
    """Return one table row."""
    failed = sum(1 for _, report in reports if not report.passed)
    normal = [report for name, report in reports if name not in LOCKOUT_SCENARIOS]
    max_run, no_progress = (
        sum(1 for report in normal if code in report.metrics.guard_stops)
        for code in (ReasonCode.MAX_RUN, ReasonCode.NO_PROGRESS)
    )
    errors = sorted(
        error for report in normal if (error := report.metrics.worst_median_error_k) is not None
    )
    p90 = f"{errors[int(0.9 * len(errors))]:.2f}" if errors else "-"
    daily = [r.metrics.starts for name, r in reports if name in {"cold_night", "mild_day"}]
    cold = [r.metrics for name, r in reports if name == "cold_night"]
    overheat = max(report.metrics.worst_overheat_min for _, report in reports)
    return (
        f"| {variant.name} | {failed}/{len(reports)} | {max_run} | {no_progress} | "
        f"{_mean(errors, 3)} | {p90} | "
        f"{_mean(daily, 1)} | {_mean((m.mean_pct_on or 0.0 for m in cold), 0)} | "
        f"{_mean((m.distinct_pct for m in cold), 0)} | {overheat:.0f} |"
    )


HEADER = (
    "| variant | failed runs | max-run lockouts | no-progress lockouts "
    "| mean worst median error K | p90 K | starts/day | cold mean % | cold levels "
    "| worst overheat min |\n"
    "|---|---|---|---|---|---|---|---|---|---|"
)


def main(argv: Sequence[str] | None = None) -> int:
    """Print the comparison table (Markdown)."""
    parser = argparse.ArgumentParser(prog="python -m sim.tune", description=__doc__)
    parser.add_argument("--quick", action="store_true", help="default plant, seed 1 only")
    args = parser.parse_args(argv)
    plants = PLANTS[:1] if args.quick else PLANTS
    seeds = (1,) if args.quick else (1, 2, 3)
    print(f"{len(SCENARIOS)} scenarios x {len(plants)} plants x {len(seeds)} seeds per variant")
    print(HEADER)
    for variant in VARIANTS:
        print(summarise(variant, run_variant(variant, plants, seeds)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
