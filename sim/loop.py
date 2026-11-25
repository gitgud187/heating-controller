"""
The simulation loop: step the plant, call the engine every 60 s, apply its command, record.

The engine state goes through `to_dict`/`from_dict` on every evaluation, as the integration
persists it, so a restart is just a gap without evaluations.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from custom_components.heating_controller.engine.config import EngineConfig
from custom_components.heating_controller.engine.evaluate import Evaluation, evaluate
from custom_components.heating_controller.engine.model import Controls, RoomSnapshot, Snapshot
from custom_components.heating_controller.engine.state import EngineState

from .devices import (
    Board,
    BoardParams,
    FailsafeCause,
    Sensor,
    SensorFault,
    SensorParams,
    ThermostatSim,
    ValveFault,
    ValveSim,
)
from .plant import OutdoorProfile, ThermalPlant, effective_output_pct

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from custom_components.heating_controller.engine.demand import RoomStatus
    from custom_components.heating_controller.engine.reasons import ReasonCode

    from .house import HouseSpec

EVALUATION_INTERVAL = timedelta(minutes=1)
PLANT_STEPS_PER_EVALUATION = 2
ALL_ROOMS = "*"


@dataclass(frozen=True, slots=True)
class WindowOpen:
    """A window opens in a room for a while; VTherm sees it and holds the valve off."""

    room: str
    at: timedelta
    duration: timedelta


@dataclass(frozen=True, slots=True)
class WallSensorFault:
    """A wall sensor dies or freezes; `duration` None lasts to the end."""

    room: str
    at: timedelta
    fault: SensorFault
    duration: timedelta | None = None


@dataclass(frozen=True, slots=True)
class ValveFaultEvent:
    """A valve fault; room `*` hits every valve (the Zigbee coordinator going down)."""

    room: str
    at: timedelta
    fault: ValveFault
    duration: timedelta | None = None


class Outage(StrEnum):
    """Periods without evaluations."""

    HUNG = "hung"  # the integration stops; Home Assistant and its API stay up
    RESTART = "restart"  # Home Assistant restarts; the API is gone as well


@dataclass(frozen=True, slots=True)
class OutageEvent:
    """No evaluations (and no heartbeat) for a while."""

    kind: Outage
    at: timedelta
    duration: timedelta


@dataclass(frozen=True, slots=True)
class PauseEvent:
    """The pause switch is on for a while."""

    at: timedelta
    duration: timedelta


class Button(StrEnum):
    """The integration's buttons."""

    CLEAR_LOCKOUT = "clear_lockout"


@dataclass(frozen=True, slots=True)
class Press:
    """A button press, seen by the next evaluation."""

    button: Button
    at: timedelta


type Event = WindowOpen | WallSensorFault | ValveFaultEvent | OutageEvent | PauseEvent | Press


@dataclass(frozen=True, slots=True)
class Criteria:
    """
    Which spec 13 criteria a scenario must meet.

    `error_rooms`: rooms whose median error is checked (None: every room). `overheat_rooms`
    likewise. A check set to None is skipped.
    """

    max_median_error_k: float | None = 0.3
    error_rooms: tuple[str, ...] | None = None
    overheat_k: float = 0.8
    overheat_for: timedelta = timedelta(minutes=30)
    check_overheat: bool = True
    overheat_rooms: tuple[str, ...] | None = None
    settle_after_change: timedelta = timedelta(hours=2)
    min_run: timedelta = timedelta(minutes=10)
    min_distinct_pct: int | None = 5
    allow_max_run: bool = False


type Check = Callable[["SimResult"], list[str]]


@dataclass(frozen=True, slots=True)
class Scenario:
    """
    One simulated situation: weather, house, events, engine config and what must hold.

    Metrics ignore the first `settle` of the run (the start is an arbitrary state); the
    guard limits are checked over the whole run.
    """

    name: str
    description: str
    start: datetime
    duration: timedelta
    outdoor: OutdoorProfile
    house: HouseSpec
    events: tuple[Event, ...] = ()
    config: EngineConfig = field(default_factory=EngineConfig)
    seed: int = 1
    settle: timedelta = timedelta(hours=3)
    criteria: Criteria = field(default_factory=Criteria)
    checks: tuple[Check, ...] = ()
    initial_offset_k: float = -0.1


@dataclass(frozen=True, slots=True)
class RoomSample:
    """One room at one minute."""

    name: str
    temp_c: float
    wall_c: float | None
    target_c: float
    opening: float
    window_open: bool
    status: RoomStatus | None
    load_m2k: float
    faulted: bool


@dataclass(frozen=True, slots=True)
class Sample:
    """
    The house at one minute, after the minute's evaluation (if there was one).

    `heating` and `power_pct` are the board's physical state; `failsafe` is set when the
    board switched off on its own during the minute.
    """

    time: datetime
    outdoor_c: float
    heating: bool
    power_pct: int
    evaluated: bool
    reasons: tuple[ReasonCode, ...]
    any_valve_open: bool
    load_m2k: float
    error_k: float
    base_pct: float
    p_pct: float
    integral_pct: float
    paused: bool
    lockout_until: datetime | None
    failsafe: FailsafeCause | None
    rooms: tuple[RoomSample, ...]


@dataclass(frozen=True, slots=True)
class SimResult:
    """Everything a scenario produced."""

    scenario: Scenario
    samples: tuple[Sample, ...]
    restored_states: int


@dataclass
class _Active:
    """Event state at one moment."""

    windows: set[str] = field(default_factory=set)
    sensor_faults: dict[str, SensorFault] = field(default_factory=dict)
    valve_faults: dict[str, ValveFault] = field(default_factory=dict)
    outage: Outage | None = None
    paused: bool = False


def _active(events: tuple[Event, ...], offset: timedelta) -> _Active:
    """Which events are in force `offset` after the start."""
    active = _Active()

    def during(at: timedelta, duration: timedelta | None) -> bool:
        return at <= offset and (duration is None or offset < at + duration)

    for event in events:
        match event:
            case WindowOpen(room, at, duration) if during(at, duration):
                active.windows.add(room)
            case WallSensorFault(room, at, fault, duration) if during(at, duration):
                active.sensor_faults[room] = fault
            case ValveFaultEvent(room, at, fault, duration) if during(at, duration):
                active.valve_faults[room] = fault
            case OutageEvent(kind, at, duration) if during(at, duration):
                active.outage = kind
            case PauseEvent(at, duration) if during(at, duration):
                active.paused = True
            case _:
                pass
    return active


class Simulation:
    """Runs one scenario minute by minute."""

    def __init__(self, scenario: Scenario) -> None:
        """Build the plant and the devices; the engine starts from an empty state."""
        self.scenario = scenario
        self.rng = random.Random(scenario.seed)  # noqa: S311 - reproducible simulation noise
        start = scenario.start
        house = scenario.house
        initial = {
            spec.params.name: spec.schedule.target_at(start) + scenario.initial_offset_k
            for spec in house.rooms
        }
        self.plant = ThermalPlant(
            tuple(spec.params for spec in house.rooms), house.couplings, initial
        )
        self.sensors = {
            spec.params.name: Sensor(spec.sensor, start=start, phase=timedelta(seconds=37 * index))
            for index, spec in enumerate(house.rooms)
        }
        self.outdoor_sensor = Sensor(
            SensorParams(interval=timedelta(minutes=5), noise_k=0.05),
            start=start,
            phase=timedelta(),
        )
        self.valves = {spec.params.name: ValveSim(spec.valve, start=start) for spec in house.rooms}
        self.thermostats = {
            spec.params.name: ThermostatSim(
                spec.schedule, target_c=spec.schedule.target_at(start), last_reported=start
            )
            for spec in house.rooms
        }
        self.board = Board(BoardParams(), start=start)
        self.stored: Mapping[str, object] = EngineState().to_dict()
        self.handled_presses: set[tuple[Button, timedelta]] = set()
        self.restored = 0

    def run(self) -> SimResult:
        """Run to the end and return the record."""
        scenario = self.scenario
        steps = int(scenario.duration / EVALUATION_INTERVAL)
        samples = [self._minute(index * EVALUATION_INTERVAL) for index in range(steps)]
        return SimResult(scenario=scenario, samples=tuple(samples), restored_states=self.restored)

    def _minute(self, offset: timedelta) -> Sample:
        scenario = self.scenario
        now = scenario.start + offset
        active = _active(scenario.events, offset)
        self._apply_faults(active)
        self._update_devices(now)

        evaluation: Evaluation | None = None
        if active.outage is None:
            evaluation = self._evaluate(now, active, offset)
        self.board.set_api(now, connected=active.outage is not Outage.RESTART)

        failsafe = None
        sub = EVALUATION_INTERVAL / PLANT_STEPS_PER_EVALUATION
        outdoor_c = scenario.outdoor.at(now)
        for index in range(PLANT_STEPS_PER_EVALUATION):
            moment = now + index * sub
            failsafe = self.board.tick(moment) or failsafe
            if index:
                self._update_devices(moment)
            self.plant.step(
                sub,
                outdoor_c=scenario.outdoor.at(moment),
                output_pct=effective_output_pct(
                    heating=self.board.heating, request_pct=self.board.power_pct
                ),
                openings={name: valve.opening for name, valve in self.valves.items()},
                windows_open={name: name in active.windows for name in self.valves},
            )
        return self._sample(now, outdoor_c, active, evaluation, failsafe)

    def _apply_faults(self, active: _Active) -> None:
        everyone = active.valve_faults.get(ALL_ROOMS)
        for name, valve in self.valves.items():
            valve.fault = everyone or active.valve_faults.get(name, ValveFault.NONE)
        for name, sensor in self.sensors.items():
            sensor.fault = active.sensor_faults.get(name, SensorFault.NONE)
        for name, thermostat in self.thermostats.items():
            thermostat.window_open = name in active.windows

    def _update_devices(self, now: datetime) -> None:
        temps = self.plant.temps_c
        self.outdoor_sensor.update(now, self.scenario.outdoor.at(now), self.rng)
        for name, sensor in self.sensors.items():
            reported = sensor.update(now, temps[name], self.rng)
            thermostat = self.thermostats[name]
            thermostat.update(now, wall_reported=reported and sensor.value is not None)
            self.valves[name].update(now, temps[name], thermostat.valve_target(), self.rng)

    def _evaluate(self, now: datetime, active: _Active, offset: timedelta) -> Evaluation:
        scenario = self.scenario
        snapshot = Snapshot(
            now=now,
            taken_at=now,
            complete=True,
            rooms=tuple(
                RoomSnapshot(
                    name=spec.params.name,
                    area_m2=spec.params.area_m2,
                    thermostat=self.thermostats[spec.params.name].engine_view(),
                    wall=self.sensors[spec.params.name].reading(),
                    valve=self.valves[spec.params.name].engine_view(),
                )
                for spec in scenario.house.rooms
            ),
            outdoor=self.outdoor_sensor.reading(),
            boiler_on=self.board.heating,
        )
        presses = {
            event.button
            for event in scenario.events
            if isinstance(event, Press)
            and event.at <= offset
            and (event.button, event.at) not in self.handled_presses
        }
        self.handled_presses.update(
            (event.button, event.at)
            for event in scenario.events
            if isinstance(event, Press) and event.at <= offset
        )
        controls = Controls(
            paused=active.paused,
            clear_lockout=Button.CLEAR_LOCKOUT in presses,
        )
        state = EngineState.from_dict(self.stored)
        self.restored += 1
        result = evaluate(snapshot, state, scenario.config, controls)
        self.stored = result.state.to_dict()
        self.board.press_heartbeat(now)
        if result.command is not None:
            self.board.write(heat=result.command.heat, power_pct=result.command.modulation_pct)
        return result

    def _sample(
        self,
        now: datetime,
        outdoor_c: float,
        active: _Active,
        evaluation: Evaluation | None,
        failsafe: FailsafeCause | None,
    ) -> Sample:
        assessments = {} if evaluation is None else {room.name: room for room in evaluation.rooms}
        rooms = []
        for spec in self.scenario.house.rooms:
            name = spec.params.name
            assessment = assessments.get(name)
            rooms.append(
                RoomSample(
                    name=name,
                    temp_c=self.plant.temps_c[name],
                    wall_c=self.sensors[name].value,
                    target_c=self.thermostats[name].target_c,
                    opening=self.valves[name].opening,
                    window_open=name in active.windows,
                    status=None if assessment is None else assessment.status,
                    load_m2k=0.0 if assessment is None else assessment.load_m2k,
                    faulted=name in active.sensor_faults
                    or name in active.valve_faults
                    or ALL_ROOMS in active.valve_faults,
                )
            )
        state = EngineState.from_dict(self.stored)
        modulation = None if evaluation is None else evaluation.modulation
        return Sample(
            time=now,
            outdoor_c=outdoor_c,
            heating=self.board.heating,
            power_pct=self.board.power_pct,
            evaluated=evaluation is not None,
            reasons=() if evaluation is None else tuple(r.code for r in evaluation.reasons),
            any_valve_open=evaluation is not None
            and any(room.valve.open for room in evaluation.rooms),
            load_m2k=0.0 if evaluation is None else evaluation.load_m2k,
            error_k=0.0 if modulation is None else modulation.error_k,
            base_pct=0.0 if modulation is None else modulation.base_pct,
            p_pct=0.0 if modulation is None else modulation.p_pct,
            integral_pct=state.integral_pct,
            paused=active.paused,
            lockout_until=state.lockout_until,
            failsafe=failsafe,
            rooms=tuple(rooms),
        )


def simulate(scenario: Scenario) -> SimResult:
    """Run one scenario."""
    return Simulation(scenario).run()
