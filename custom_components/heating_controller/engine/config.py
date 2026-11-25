"""Thresholds and gains. Defaults are the values of the specification."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from .validity import DEFAULT_STALE_AFTER


@dataclass(frozen=True, slots=True)
class ValidityConfig:
    """When inputs can be trusted."""

    stale_after: timedelta = DEFAULT_STALE_AFTER


@dataclass(frozen=True, slots=True)
class DemandConfig:
    """Thermal demand hysteresis and the blocked-room grace."""

    needs_heat_k: float = 0.2
    blocked_after: timedelta = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class ValveConfig:
    """What counts as an open and as a healthy valve, per profile."""

    moes_open_position_pct: float = 5.0
    moes_healthy_preset: str = "manual"
    moes_healthy_mode: str = "comfort"
    sonoff_healthy_system_mode: str = "heat"
    sonoff_required_degree_pct: float = 100.0
    sonoff_healthy_smart_temperature_control: str = "off"
    sonoff_healthy_sensor_select: str = "local_temperature"


@dataclass(frozen=True, slots=True)
class BoilerConfig:
    """
    The core state machine: start, starvation start, stop.

    `external_stop_grace`: how long after a start the switch may still read off without that
    counting as a stop made outside the engine.
    """

    start_load_m2k: float = 4.0
    stop_load_m2k: float = 1.5
    min_on: timedelta = timedelta(minutes=10)
    starvation_after: timedelta = timedelta(minutes=45)
    external_stop_grace: timedelta = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class ModulationConfig:
    """Heating curve, PI gains, anti-windup and output rounding."""

    mild_outdoor_c: float = 10.0
    mild_base_pct: float = 30.0
    cold_outdoor_c: float = -5.0
    cold_base_pct: float = 70.0
    fallback_outdoor_c: float = 0.0
    kp_pct_per_k: float = 15.0
    ki_pct_per_k_h: float = 10.0
    integral_limit_pct: float = 30.0
    min_pct: int = 30
    max_pct: int = 100
    integral_delay: timedelta = timedelta(minutes=10)
    decay_time_constant: timedelta = timedelta(hours=6)
    max_integration_step: timedelta = timedelta(minutes=5)
    write_step_pct: int = 2


@dataclass(frozen=True, slots=True)
class GuardConfig:
    """Hard limits that always win over the core."""

    max_starts: int = 3
    start_window: timedelta = timedelta(hours=1)
    min_off: timedelta = timedelta(minutes=8)
    max_run: timedelta = timedelta(hours=3)
    lockout: timedelta = timedelta(hours=1)
    no_open_valve_after: timedelta = timedelta(minutes=3)
    snapshot_max_age: timedelta = timedelta(minutes=2)
    min_pct: int = 30
    max_pct: int = 100
    idle_pct: int = 30


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """Everything the engine can be tuned with."""

    validity: ValidityConfig = field(default_factory=ValidityConfig)
    demand: DemandConfig = field(default_factory=DemandConfig)
    valves: ValveConfig = field(default_factory=ValveConfig)
    boiler: BoilerConfig = field(default_factory=BoilerConfig)
    modulation: ModulationConfig = field(default_factory=ModulationConfig)
    guard: GuardConfig = field(default_factory=GuardConfig)
