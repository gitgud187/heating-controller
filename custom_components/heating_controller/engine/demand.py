"""Room demand: thermal demand with hysteresis, exclusion, served and blocked (spec 6)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from .model import ThermostatMode
from .validity import fresh_value
from .valves import ValveAssessment, assess_valve

if TYPE_CHECKING:
    from datetime import datetime

    from .config import EngineConfig
    from .model import RoomSnapshot, Thermostat

# Sensors report in 0.1 K steps; rounding keeps 21.0 - 20.8 from landing just below 0.2.
_ERROR_DECIMALS = 6


class RoomStatus(StrEnum):
    """One status per room and evaluation, in order of precedence."""

    WALL_INVALID = "wall_invalid"
    THERMOSTAT_INVALID = "thermostat_invalid"
    VALVE_DATA_MISSING = "valve_data_missing"
    THERMOSTAT_OFF = "thermostat_off"
    WINDOW_OPEN = "window_open"
    SATISFIED = "satisfied"
    SERVED = "served"
    WAITING_FOR_VALVE = "waiting_for_valve"
    VALVE_SETTLED = "valve_settled"
    BLOCKED = "blocked"


EXCLUDED = frozenset(
    {
        RoomStatus.WALL_INVALID,
        RoomStatus.THERMOSTAT_INVALID,
        RoomStatus.VALVE_DATA_MISSING,
    }
)


@dataclass(frozen=True, slots=True)
class RoomState:
    """What a room must remember between evaluations."""

    needs_heat: bool = False
    blocked_since: datetime | None = None


@dataclass(frozen=True, slots=True)
class RoomAssessment:
    """One room's result: status, demand, valve and its share of the load."""

    name: str
    area_m2: float
    status: RoomStatus
    needs_heat: bool
    valve: ValveAssessment
    error_k: float | None
    state: RoomState

    @property
    def excluded(self) -> bool:
        """The room's data cannot be trusted (fail-closed)."""
        return self.status in EXCLUDED

    @property
    def served(self) -> bool:
        """Needs heat and the valve is open or may still open (grace)."""
        return self.status in {RoomStatus.SERVED, RoomStatus.WAITING_FOR_VALVE}

    @property
    def blocked(self) -> bool:
        """Needs heat but the valve stayed closed for longer than the grace."""
        return self.status is RoomStatus.BLOCKED

    @property
    def load_m2k(self) -> float:
        """Contribution to the load: area times error for served rooms, else 0."""
        if not self.served or self.error_k is None:
            return 0.0
        return self.area_m2 * self.error_k


def needs_heat(*, previous: bool, error_k: float, needs_heat_k: float) -> bool:
    """Hysteresis: in at `error >= needs_heat_k`, out at `error <= 0`, else hold."""
    if error_k >= needs_heat_k:
        return True
    if error_k <= 0:
        return False
    return previous


def assess_room(
    room: RoomSnapshot, previous: RoomState, now: datetime, config: EngineConfig
) -> RoomAssessment:
    """Assess one room against its previous state."""
    stale_after = config.validity.stale_after
    valve = assess_valve(room.valve, config.valves)
    wall = fresh_value(room.wall, now, stale_after)
    target = fresh_value(room.thermostat.target, now, stale_after)

    def result(
        status: RoomStatus, state: RoomState, error_k: float | None = None
    ) -> RoomAssessment:
        return RoomAssessment(
            name=room.name,
            area_m2=room.area_m2,
            status=status,
            needs_heat=state.needs_heat,
            valve=valve,
            error_k=error_k,
            state=state,
        )

    if wall is None or target is None or room.thermostat.mode is None:
        status = RoomStatus.WALL_INVALID if wall is None else RoomStatus.THERMOSTAT_INVALID
        return result(status, previous)
    error_k = round(target - wall, _ERROR_DECIMALS)
    no_demand = _no_demand_status(room.thermostat)
    if no_demand is not None:
        return result(no_demand, RoomState(), error_k)

    heat = needs_heat(
        previous=previous.needs_heat, error_k=error_k, needs_heat_k=config.demand.needs_heat_k
    )
    valve_problem = _valve_status(valve)
    if valve_problem is not None:
        return result(valve_problem, RoomState(heat, previous.blocked_since), error_k)
    status, state = _demand_status(
        heat=heat,
        valve_open=valve.open,
        error_k=error_k,
        previous=previous,
        now=now,
        config=config,
    )
    return result(status, state, error_k)


def _no_demand_status(thermostat: Thermostat) -> RoomStatus | None:
    if thermostat.mode is ThermostatMode.OFF:
        return RoomStatus.THERMOSTAT_OFF
    if thermostat.window_open:
        return RoomStatus.WINDOW_OPEN
    return None


def _valve_status(valve: ValveAssessment) -> RoomStatus | None:
    if valve.data_missing:
        return RoomStatus.VALVE_DATA_MISSING
    return None


def _demand_status(  # noqa: PLR0913 - every input of the decision, by keyword
    *,
    heat: bool,
    valve_open: bool,
    error_k: float,
    previous: RoomState,
    now: datetime,
    config: EngineConfig,
) -> tuple[RoomStatus, RoomState]:
    """
    Satisfied, served, or waiting for the valve until the grace runs out.

    After the grace a room well below target is blocked; one close to target has a valve that
    settled on its own and no longer counts.
    """
    if not heat:
        return RoomStatus.SATISFIED, RoomState()
    if valve_open:
        return RoomStatus.SERVED, RoomState(needs_heat=True)
    blocked_since = previous.blocked_since or now
    state = RoomState(needs_heat=True, blocked_since=blocked_since)
    if now - blocked_since > config.demand.blocked_after:
        if error_k >= config.demand.blocked_min_k:
            return RoomStatus.BLOCKED, state
        return RoomStatus.VALVE_SETTLED, state
    return RoomStatus.WAITING_FOR_VALVE, state
