"""The user-facing tuning options and how they map onto the engine configuration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

from ..engine.config import EngineConfig

if TYPE_CHECKING:
    from collections.abc import Mapping

type Unit = Literal["m2k", "k", "min", "h", "count", "pct", "pct_per_k", "pct_per_k_h", "c"]


@dataclass(frozen=True, slots=True)
class Option:
    """One option: key, engine section and field, unit, default, bounds and step."""

    key: str
    section: str
    field: str
    unit: Unit
    default: float
    minimum: float
    maximum: float
    step: float


def _option(  # noqa: PLR0913, PLR0917 - a table row, read positionally
    key: str, section: str, field: str, unit: Unit, minimum: float, maximum: float, step: float
) -> Option:
    default = getattr(getattr(EngineConfig(), section), field)
    if isinstance(default, timedelta):
        default = default / (timedelta(hours=1) if unit == "h" else timedelta(minutes=1))
    return Option(key, section, field, unit, float(default), minimum, maximum, step)


OPTIONS: tuple[Option, ...] = (
    _option("start_load_m2k", "boiler", "start_load_m2k", "m2k", 0.5, 50, 0.5),
    _option("stop_load_m2k", "boiler", "stop_load_m2k", "m2k", 0, 50, 0.5),
    _option("starvation_after_min", "boiler", "starvation_after", "min", 5, 240, 5),
    _option("min_on_min", "boiler", "min_on", "min", 0, 60, 1),
    _option("needs_heat_k", "demand", "needs_heat_k", "k", 0.1, 2, 0.1),
    _option("satisfied_k", "demand", "satisfied_k", "k", 0, 1.9, 0.1),
    _option("blocked_after_min", "demand", "blocked_after", "min", 1, 120, 1),
    _option("blocked_min_k", "demand", "blocked_min_k", "k", 0.1, 3, 0.1),
    _option("stale_after_min", "validity", "stale_after", "min", 5, 240, 5),
    _option("min_off_min", "guard", "min_off", "min", 0, 60, 1),
    _option("max_starts", "guard", "max_starts", "count", 1, 12, 1),
    _option("progress_check_after_h", "guard", "progress_check_after", "h", 1, 12, 0.5),
    _option("progress_window_min", "guard", "progress_window", "min", 15, 180, 5),
    _option("progress_margin_k", "guard", "progress_margin_k", "k", 0.1, 2, 0.1),
    _option("max_run_h", "guard", "max_run", "h", 1, 24, 0.5),
    _option("lockout_min", "guard", "lockout", "min", 5, 240, 5),
    _option("no_open_valve_min", "guard", "no_open_valve_after", "min", 1, 30, 1),
    _option("kp_pct_per_k", "modulation", "kp_pct_per_k", "pct_per_k", 0, 100, 1),
    _option("ki_pct_per_k_h", "modulation", "ki_pct_per_k_h", "pct_per_k_h", 0, 100, 1),
    _option("mild_outdoor_c", "modulation", "mild_outdoor_c", "c", -10, 25, 0.5),
    _option("mild_base_pct", "modulation", "mild_base_pct", "pct", 30, 100, 1),
    _option("cold_outdoor_c", "modulation", "cold_outdoor_c", "c", -30, 10, 0.5),
    _option("cold_base_pct", "modulation", "cold_base_pct", "pct", 30, 100, 1),
)


def engine_config(options: Mapping[str, object]) -> EngineConfig:
    """Build the engine configuration from the stored options; missing keys keep defaults."""
    config = EngineConfig()
    sections: dict[str, dict[str, object]] = {}
    for option in OPTIONS:
        raw = options.get(option.key)
        if not isinstance(raw, int | float) or isinstance(raw, bool):
            continue
        current = getattr(getattr(config, option.section), option.field)
        value: object
        if isinstance(current, timedelta):
            value = timedelta(hours=raw) if option.unit == "h" else timedelta(minutes=raw)
        elif isinstance(current, int):
            value = round(raw)
        else:
            value = float(raw)
        sections.setdefault(option.section, {})[option.field] = value
    for section, changes in sections.items():
        config = replace(config, **{section: replace(getattr(config, section), **changes)})
    return config
