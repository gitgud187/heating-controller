"""Valve profiles: whether a valve is open, healthy, or has no data (spec 6.2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .model import MoesValve, SonoffValve, Valve
from .validity import fresh_value

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from .config import ValveConfig


@dataclass(frozen=True, slots=True)
class ValveAssessment:
    """What the engine believes about one valve. A valve without data is never open."""

    open: bool
    healthy: bool
    data_missing: bool


def assess_valve(
    valve: Valve, now: datetime, stale_after: timedelta, config: ValveConfig
) -> ValveAssessment:
    """Apply the valve's profile."""
    if isinstance(valve, MoesValve):
        return _assess_moes(valve, now, stale_after, config)
    return _assess_sonoff(valve, now, stale_after, config)


def _assess_moes(
    valve: MoesValve, now: datetime, stale_after: timedelta, config: ValveConfig
) -> ValveAssessment:
    heating = fresh_value(valve.heating, now, stale_after)
    position = fresh_value(valve.position, now, stale_after)
    position_open = position is not None and position > config.moes_open_position_pct
    return ValveAssessment(
        open=position_open or heating is True,
        healthy=valve.preset == config.moes_healthy_preset
        and valve.mode == config.moes_healthy_mode,
        data_missing=heating is None and position is None,
    )


def _assess_sonoff(
    valve: SonoffValve, now: datetime, stale_after: timedelta, config: ValveConfig
) -> ValveAssessment:
    heating = fresh_value(valve.heating, now, stale_after)
    return ValveAssessment(
        open=heating is True,
        healthy=valve.system_mode == config.sonoff_healthy_system_mode
        and valve.opening_degree_pct == config.sonoff_required_degree_pct
        and valve.closing_degree_pct == config.sonoff_required_degree_pct
        and valve.smart_temperature_control == config.sonoff_healthy_smart_temperature_control
        and valve.temperature_sensor_select == config.sonoff_healthy_sensor_select,
        data_missing=heating is None,
    )
