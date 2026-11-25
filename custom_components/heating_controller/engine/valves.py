"""
Valve profiles: whether a valve is open, healthy, or has no data (spec 6.2).

Valve data has no age limit. Radiator valves report only on change, often less than once an
hour, so an old value is normal. Data is missing only when the entity is unavailable or unknown
(value None); the Zigbee coordinator's own availability check decides when a device is gone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .model import MoesValve, SonoffValve, Valve

if TYPE_CHECKING:
    from .config import ValveConfig


@dataclass(frozen=True, slots=True)
class ValveAssessment:
    """What the engine believes about one valve. A valve without data is never open."""

    open: bool
    healthy: bool
    data_missing: bool


def assess_valve(valve: Valve, config: ValveConfig) -> ValveAssessment:
    """Apply the valve's profile."""
    if isinstance(valve, MoesValve):
        return _assess_moes(valve, config)
    return _assess_sonoff(valve, config)


def _assess_moes(valve: MoesValve, config: ValveConfig) -> ValveAssessment:
    heating = valve.heating.value
    position = valve.position.value
    position_open = position is not None and position > config.moes_open_position_pct
    return ValveAssessment(
        open=position_open or heating is True,
        healthy=valve.preset == config.moes_healthy_preset
        and valve.mode == config.moes_healthy_mode,
        data_missing=heating is None and position is None,
    )


def _assess_sonoff(valve: SonoffValve, config: ValveConfig) -> ValveAssessment:
    heating = valve.heating.value
    return ValveAssessment(
        open=heating is True,
        healthy=valve.system_mode == config.sonoff_healthy_system_mode
        and valve.opening_degree_pct == config.sonoff_required_degree_pct
        and valve.closing_degree_pct == config.sonoff_required_degree_pct
        and valve.smart_temperature_control == config.sonoff_healthy_smart_temperature_control
        and valve.temperature_sensor_select == config.sonoff_healthy_sensor_select,
        data_missing=heating is None,
    )
