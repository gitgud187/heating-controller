"""Thresholds and gains. Defaults are the values of the specification."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from .validity import DEFAULT_STALE_AFTER


@dataclass(frozen=True, slots=True)
class ValidityConfig:
    """When inputs can be trusted."""

    stale_after: timedelta = DEFAULT_STALE_AFTER
    # Outdoor sensors often report only on change, and the outdoor temperature changes slowly.
    outdoor_stale_after: timedelta = timedelta(hours=3)


@dataclass(frozen=True, slots=True)
class DemandConfig:
    """
    Thermal demand hysteresis and the blocked-room grace.

    A room whose valve stays closed while it needs heat is blocked after `blocked_after`, but
    only while it is at least `blocked_min_k` below target. Closer to target a closed valve is
    the valve settling on its own sensor (which sits by the radiator and reads warm); the room
    then stops counting towards the load without being reported.

    A room needs heat from `needs_heat_k` below target until it is no more than `satisfied_k`
    below. A positive `satisfied_k` lets the radiators' residual heat carry the room the rest of
    the way after the boiler stops; it must stay below `needs_heat_k`.
    """

    needs_heat_k: float = 0.2
    satisfied_k: float = 0.0
    blocked_after: timedelta = timedelta(minutes=30)
    blocked_min_k: float = 0.5


@dataclass(frozen=True, slots=True)
class ValveConfig:
    """When a reported valve opening counts as open."""

    open_position_pct: float = 5.0


@dataclass(frozen=True, slots=True)
class BoilerConfig:
    """
    The core state machine: start, starvation start, stop.

    `external_stop_grace`: how long after a start the switch may still read off without that
    counting as a stop made outside the engine. `excluded_hold`: see below.
    """

    start_load_m2k: float = 4.0
    stop_load_m2k: float = 1.5
    min_on: timedelta = timedelta(minutes=10)
    starvation_after: timedelta = timedelta(minutes=45)
    external_stop_grace: timedelta = timedelta(seconds=30)
    # A running boiler keeps running this long while no room has trustworthy data. After a
    # Home Assistant restart the thermostats and valves need up to a minute to come back; a
    # short Zigbee outage looks the same. Data that stays away still stops the boiler.
    excluded_hold: timedelta = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class ModulationConfig:
    """Heating curve, PI gains, anti-windup and output rounding."""

    mild_outdoor_c: float = 10.0
    mild_base_pct: float = 30.0
    cold_outdoor_c: float = -5.0
    cold_base_pct: float = 70.0
    fallback_outdoor_c: float = 0.0
    # A satisfied room counts towards the error with at most this much above target.
    satisfied_floor_k: float = -0.5
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
    """
    Hard limits that always win over the core.

    A run that has lasted `progress_check_after` is stopped with a lockout as soon as no room
    that was served `progress_window` ago has reached its target or closed its gap by at least
    `progress_margin_k`; any run is stopped with a lockout at `max_run`. The served rooms' gaps
    are sampled at most every `progress_sample_interval`, rounded to
    `progress_sample_decimals`, and kept for the window plus `progress_history_slack` (which
    bridges a restart of a few minutes).
    """

    max_starts: int = 3
    start_window: timedelta = timedelta(hours=1)
    min_off: timedelta = timedelta(minutes=8)
    max_run: timedelta = timedelta(hours=6)
    progress_check_after: timedelta = timedelta(hours=3)
    progress_window: timedelta = timedelta(minutes=60)
    progress_margin_k: float = 0.2
    progress_sample_interval: timedelta = timedelta(minutes=1)
    progress_sample_decimals: int = 2
    progress_history_slack: timedelta = timedelta(minutes=10)
    lockout: timedelta = timedelta(hours=1)
    no_open_valve_after: timedelta = timedelta(minutes=3)
    snapshot_max_age: timedelta = timedelta(minutes=2)
    min_pct: int = 30
    max_pct: int = 100
    idle_pct: int = 30


@dataclass(frozen=True, slots=True)
class OverheatConfig:
    """A room is overheating when it stays `above_k` over its target for `after`."""

    above_k: float = 1.5
    after: timedelta = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """Everything the engine can be tuned with."""

    validity: ValidityConfig = field(default_factory=ValidityConfig)
    demand: DemandConfig = field(default_factory=DemandConfig)
    valves: ValveConfig = field(default_factory=ValveConfig)
    boiler: BoilerConfig = field(default_factory=BoilerConfig)
    modulation: ModulationConfig = field(default_factory=ModulationConfig)
    guard: GuardConfig = field(default_factory=GuardConfig)
    overheat: OverheatConfig = field(default_factory=OverheatConfig)
