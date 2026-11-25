"""Seeded random runs through evaluate(): the guard limits hold over days of noisy inputs."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest

from custom_components.heating_controller.engine.config import EngineConfig
from custom_components.heating_controller.engine.evaluate import evaluate
from custom_components.heating_controller.engine.model import (
    Controls,
    RoomSnapshot,
    ThermostatMode,
    Valve,
)
from custom_components.heating_controller.engine.reasons import ReasonCode
from custom_components.heating_controller.engine.state import EngineState

from .factories import NOW, moes, room, snapshot, sonoff

CONFIG = EngineConfig()
GUARD = CONFIG.guard
STEP = timedelta(minutes=1)
STEPS = 2 * 24 * 60


@dataclass
class SimRoom:
    name: str
    area_m2: float
    sonoff: bool
    wall: float
    target: float = 21.0
    valve_open: bool = False
    window_open: bool = False
    wall_dead: bool = False

    def snapshot(self, at: datetime) -> RoomSnapshot:
        valve: Valve
        if self.sonoff:
            valve = sonoff(heating=self.valve_open, at=at)
        else:
            valve = moes(heating=self.valve_open, position=50.0 if self.valve_open else 0.0, at=at)
        return room(
            self.name,
            area_m2=self.area_m2,
            target=self.target,
            wall=None if self.wall_dead else round(self.wall, 1),
            mode=ThermostatMode.HEAT,
            window_open=self.window_open,
            valve=valve,
            at=at,
        )


@dataclass
class History:
    starts: list[datetime] = field(default_factory=list)
    last_stop: datetime | None = None
    on_since: datetime | None = None
    lockout_end: datetime | None = None


def _chance(rng: random.Random, probability: float) -> bool:
    return rng.random() < probability


def _move_rooms(rng: random.Random, rooms: list[SimRoom], *, heating: bool) -> None:
    for sim in rooms:
        gain = 0.04 if heating and sim.valve_open else 0.0
        sim.wall += gain - 0.01 + rng.uniform(-0.01, 0.01)
        sim.valve_open = sim.wall < sim.target - 0.2 if _chance(rng, 0.9) else sim.valve_open
        if _chance(rng, 0.005):
            sim.target = rng.choice([17.0, 19.0, 21.0, 22.0])
        if _chance(rng, 0.003):
            sim.window_open = not sim.window_open
        if _chance(rng, 0.002):
            sim.wall_dead = not sim.wall_dead


def _observed_boiler(rng: random.Random, state: EngineState) -> bool | None:
    """Mostly what the engine commanded; sometimes unavailable or switched off by the board."""
    if _chance(rng, 0.002):
        return None
    if _chance(rng, 0.005):
        return False
    return state.boiler_on


def _random_controls(rng: random.Random, paused: bool) -> Controls:  # noqa: FBT001 - helper
    return Controls(
        paused=paused,
        clear_lockout=_chance(rng, 0.002),
        run_now=_chance(rng, 0.003),
    )


def _check_start(now: datetime, history: History, controls: Controls, *, open_valve: bool) -> None:
    recent = [start for start in history.starts if now - start < GUARD.start_window]
    assert len(recent) < GUARD.max_starts
    assert history.last_stop is None or now - history.last_stop >= GUARD.min_off
    assert history.lockout_end is None or now >= history.lockout_end
    assert not controls.paused
    assert open_valve


@pytest.mark.parametrize("seed", range(6))
def test_limits_hold_over_two_noisy_days(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - reproducible test data, not cryptography
    rooms = [
        SimRoom("bedroom", 16.0, sonoff=False, wall=19.0),
        SimRoom("kitchen", 5.5, sonoff=False, wall=20.0),
        SimRoom("living_room", 18.0, sonoff=True, wall=20.5),
    ]
    state = EngineState()
    history = History()
    paused = False
    now = NOW
    seen: set[ReasonCode] = set()
    for _ in range(STEPS):
        if _chance(rng, 0.002):
            paused = not paused
        controls = _random_controls(rng, paused)
        if controls.clear_lockout:
            history.lockout_end = None
        snap = snapshot(
            *(sim.snapshot(now) for sim in rooms),
            now=now,
            outdoor=rng.choice([None, -5.0, 0.0, 5.0, 12.0]),
            boiler_on=_observed_boiler(rng, state),
            complete=not _chance(rng, 0.01),
            taken_at=now - timedelta(minutes=3) if _chance(rng, 0.01) else now,
        )
        result = evaluate(snap, state, CONFIG, controls)
        seen.update(reason.code for reason in result.reasons)
        # persist and restore every step, as a restart would
        state = EngineState.from_dict(result.state.to_dict())
        assert state == result.state

        command = result.command
        if snap.boiler_on is None:
            assert command is None
        heat = command is not None and command.heat
        if heat:
            assert command is not None
            assert GUARD.min_pct <= command.modulation_pct <= GUARD.max_pct
        elif command is not None:
            assert command.modulation_pct == GUARD.idle_pct
        if paused:
            assert not heat

        if heat and history.on_since is None:
            assert snap.complete
            _check_start(now, history, controls, open_valve=any(sim.valve_open for sim in rooms))
            history.starts.append(now)
            history.on_since = now
        elif heat:
            assert history.on_since is not None
            assert now - history.on_since < GUARD.max_run
        elif history.on_since is not None:
            history.last_stop = now
            history.on_since = None
            if result.reason.code is ReasonCode.MAX_RUN:
                history.lockout_end = now + GUARD.lockout

        _move_rooms(rng, rooms, heating=heat)
        now += STEP

    assert history.starts, "the scenario should exercise at least one run"
    SEEN.update(seen)


SEEN: set[ReasonCode] = set()


def test_random_runs_reach_every_decision() -> None:
    # runs after the parametrized scenarios above (pytest keeps file order)
    assert set(ReasonCode) == SEEN
