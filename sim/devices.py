"""
Simulated devices: wall sensors, radiator valves, room thermostats and the boiler board.

Each device keeps what Home Assistant would see (value and `last_reported`) apart from the
physical truth, so the engine's validity rules are exercised the way they are in a real home.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from custom_components.heating_controller.engine.model import (
    Thermostat,
    ThermostatMode,
    Valve,
)
from custom_components.heating_controller.engine.validity import Flag, Reading

if TYPE_CHECKING:
    import random


BOARD_DEFAULT_PCT = 30


class SensorFault(StrEnum):
    """What can go wrong with a wall sensor."""

    NONE = "none"
    DEAD = "dead"  # the entity is unavailable
    FROZEN = "frozen"  # the device stops reporting; Home Assistant keeps the last value


@dataclass(frozen=True, slots=True)
class SensorParams:
    """
    How a sensor measures and reports.

    It reports every `interval`; with `change_k` set it also reports as soon as the
    quantised value moved by at least that much (and `min_interval` has passed).
    """

    interval: timedelta = timedelta(minutes=5)
    change_k: float | None = None
    min_interval: timedelta = timedelta(seconds=30)
    noise_k: float = 0.03
    resolution_k: float = 0.1
    offset_k: float = 0.0


def quantise(value: float, step: float) -> float:
    """Round to the sensor's resolution, without binary noise in the result."""
    return round(round(value / step) * step, 6)


class Sensor:
    """A temperature sensor as Home Assistant sees it."""

    def __init__(self, params: SensorParams, *, start: datetime, phase: timedelta) -> None:
        """`phase` shifts the report schedule so sensors do not all report at once."""
        self.params = params
        self.fault = SensorFault.NONE
        self.value: float | None = None
        self.last_reported = start - params.interval
        self._next_periodic = start + phase

    def update(self, now: datetime, true_c: float, rng: random.Random) -> bool:
        """Measure and report if due. Return whether a report happened."""
        if self.fault is SensorFault.FROZEN:
            return False
        if self.fault is SensorFault.DEAD:
            if self.value is not None:
                self.value = None
                self.last_reported = now
                return True
            return False
        measured = quantise(
            true_c + self.params.offset_k + rng.gauss(0.0, self.params.noise_k),
            self.params.resolution_k,
        )
        due = now >= self._next_periodic or self.value is None
        changed = (
            self.params.change_k is not None
            and self.value is not None
            and abs(measured - self.value) >= self.params.change_k - 1e-9
            and now - self.last_reported >= self.params.min_interval
        )
        if not (due or changed):
            return False
        if due:
            self._next_periodic = now + self.params.interval
        self.value = measured
        self.last_reported = now
        return True

    def reading(self) -> Reading:
        """Return the engine's view."""
        return Reading(self.value, self.last_reported)


class ValveFault(StrEnum):
    """What can go wrong with a radiator valve."""

    NONE = "none"
    STUCK_OPEN = "stuck_open"  # held open whatever the target: a mechanical or setting fault
    NEVER_OPENS = "never_opens"  # mechanically stuck, reports closed
    UNAVAILABLE = "unavailable"  # the Zigbee device is gone


class ValveKind(StrEnum):
    """How the simulated valve regulates and what it reports."""

    PROPORTIONAL = "proportional"  # modulates and reports a position (like a Moes TRV801)
    ON_OFF = "on_off"  # open or closed, reports only heating (like a Sonoff TRVZB)


@dataclass(frozen=True, slots=True)
class ValveParams:
    """
    A valve's regulation on its own sensor.

    Proportional: proportional-integral like the valve's own regulation, opening by `1 / band_k` per
    kelvin below target plus an integral with `integral_time`, positions in
    `position_step_pct` steps. On/off: opens at target + `accuracy_k` (negative),
    closes at target. `offset_k` is the valve sensor's error against the room after
    calibration.
    """

    kind: ValveKind
    band_k: float = 1.0
    integral_time: timedelta = timedelta(minutes=45)
    position_step_pct: int = 5
    accuracy_k: float = -0.2
    offset_k: float = 0.0
    noise_k: float = 0.02
    sample_interval: timedelta = timedelta(minutes=1)


class ValveSim:
    """A radiator valve: physical opening plus what it reports (only on change)."""

    def __init__(self, params: ValveParams, *, start: datetime) -> None:
        """Start closed and reported."""
        self.params = params
        self.fault = ValveFault.NONE
        self.opening = 0.0
        self.integral = 0.0
        self.position_pct = 0
        self.heating = False
        self.position_reported = start
        self.heating_reported = start
        self._next_sample = start

    def update(
        self, now: datetime, room_c: float, target_c: float | None, rng: random.Random
    ) -> None:
        """Regulate on the local sensor. `target_c` is None while the thermostat holds it off."""
        if now < self._next_sample:
            return
        self._next_sample = now + self.params.sample_interval
        local = room_c + self.params.offset_k + rng.gauss(0.0, self.params.noise_k)
        if self.fault is ValveFault.STUCK_OPEN:
            opening = 1.0
        elif self.fault is ValveFault.NEVER_OPENS or target_c is None:
            opening = 0.0
        elif self.params.kind is ValveKind.PROPORTIONAL:
            opening = self._moes_opening(target_c - local)
        elif local <= target_c + self.params.accuracy_k:
            opening = 1.0
        elif local >= target_c:
            opening = 0.0
        else:
            opening = self.opening
        self.opening = opening
        position = round(opening * 100)
        if position != self.position_pct:
            self.position_pct = position
            self.position_reported = now
        heating = position > 0
        if heating != self.heating:
            self.heating = heating
            self.heating_reported = now

    def _moes_opening(self, error_k: float) -> float:
        proportional = error_k / self.params.band_k
        step = self.params.sample_interval / self.params.integral_time
        integral = max(0.0, min(1.0, self.integral + proportional * step))
        raw = proportional + integral
        if 0.0 < raw < 1.0 or (raw <= 0 and error_k > 0) or (raw >= 1 and error_k < 0):
            self.integral = integral
        position_step = self.params.position_step_pct
        return round(max(0.0, min(1.0, raw)) * 100 / position_step) * position_step / 100

    def engine_view(self) -> Valve:
        """Return what the snapshot sees: the heating flag, and the position if reported."""
        gone = self.fault is ValveFault.UNAVAILABLE
        heating = Flag(None if gone else self.heating, self.heating_reported)
        if self.params.kind is ValveKind.PROPORTIONAL:
            position = Reading(None if gone else float(self.position_pct), self.position_reported)
            return Valve(heating=heating, position=position)
        return Valve(heating=heating)


@dataclass(frozen=True, slots=True)
class Schedule:
    """
    Room targets by time of day: (hour, target) switch points, repeated daily.

    `overrides` are absolute (from, until, target) periods that win, e.g. away.
    """

    points: tuple[tuple[float, float], ...]
    overrides: tuple[tuple[datetime, datetime, float], ...] = ()

    def target_at(self, moment: datetime) -> float:
        """Return the target at a moment."""
        for start, end, target in self.overrides:
            if start <= moment < end:
                return target
        hours = moment.hour + moment.minute / 60
        ordered = sorted(self.points)
        current = ordered[-1][1]
        for hour, target in ordered:
            if hours >= hour:
                current = target
        return current


@dataclass
class ThermostatSim:
    """
    The room thermostat (VTherm): target from the schedule, window flag, mode.

    VTherm writes its state whenever its temperature sensor reports, so its `last_reported`
    follows the wall sensor's reports as well as its own changes.
    """

    schedule: Schedule
    mode: ThermostatMode = ThermostatMode.HEAT
    window_open: bool = False
    target_c: float = 20.0
    last_reported: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=UTC))

    def update(self, now: datetime, *, wall_reported: bool) -> None:
        """Follow the schedule; re-report with the wall sensor."""
        target = self.schedule.target_at(now)
        if target != self.target_c or wall_reported:
            self.target_c = target
            self.last_reported = now

    def valve_target(self) -> float | None:
        """Return the valve's setpoint: None while off or the window is open."""
        if self.mode is ThermostatMode.OFF or self.window_open:
            return None
        return self.target_c

    def engine_view(self) -> Thermostat:
        """Return what the snapshot sees."""
        return Thermostat(
            mode=self.mode,
            target=Reading(self.target_c, self.last_reported),
            window_open=self.window_open,
        )


class FailsafeCause(StrEnum):
    """Why the board turned heating off on its own."""

    HEARTBEAT = "heartbeat"
    API = "api"


@dataclass(frozen=True, slots=True)
class BoardParams:
    """The boiler board's failsafes (spec 11)."""

    heartbeat_timeout: timedelta = timedelta(minutes=5)
    api_timeout: timedelta = timedelta(minutes=5)


class Board:
    """The boiler board: heating switch, power level, heartbeat and API failsafes."""

    def __init__(self, params: BoardParams, *, start: datetime) -> None:
        """Start with the switch off (`restore_mode: ALWAYS_OFF`)."""
        self.params = params
        self.heating = False
        self.power_pct = BOARD_DEFAULT_PCT
        self.last_heartbeat = start
        self.api_lost_since: datetime | None = None
        self.failsafe: FailsafeCause | None = None

    def press_heartbeat(self, now: datetime) -> None:
        """Take a press of the heartbeat button."""
        self.last_heartbeat = now

    def write(self, *, heat: bool, power_pct: int) -> None:
        """Take a command: heating switch and power level."""
        self.heating = heat
        self.power_pct = power_pct
        if heat:
            self.failsafe = None

    def set_api(self, now: datetime, *, connected: bool) -> None:
        """Home Assistant's API client connects or goes away."""
        if connected:
            self.api_lost_since = None
        elif self.api_lost_since is None:
            self.api_lost_since = now

    def tick(self, now: datetime) -> FailsafeCause | None:
        """Apply the failsafes; return the cause when one switched heating off now."""
        if not self.heating:
            return None
        cause: FailsafeCause | None = None
        if now - self.last_heartbeat >= self.params.heartbeat_timeout:
            cause = FailsafeCause.HEARTBEAT
        elif (
            self.api_lost_since is not None and now - self.api_lost_since >= self.params.api_timeout
        ):
            cause = FailsafeCause.API
        if cause is not None:
            self.heating = False
            self.failsafe = cause
        return cause
