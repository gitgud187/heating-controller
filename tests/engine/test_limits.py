"""Seeded random runs through evaluate(): the guard limits hold over days of noisy inputs."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest

from custom_components.heating_controller.engine.config import EngineConfig
from custom_components.heating_controller.engine.demand import RoomStatus
from custom_components.heating_controller.engine.evaluate import Evaluation, evaluate
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
    """What the boiler really did, to check the guard limits independently of the engine."""

    starts: list[datetime] = field(default_factory=list)
    last_stop: datetime | None = None
    on_since: datetime | None = None
    lockout_end: datetime | None = None
    run_gaps: dict[datetime, dict[str, float]] = field(default_factory=dict)

    def observe(
        self,
        now: datetime,
        result: Evaluation,
        controls: Controls,
        *,
        complete: bool,
        open_valve: bool,
    ) -> None:
        if result.command is None:
            return  # boiler unavailable: nothing commanded, the run is neither ended nor begun
        heat = result.command.heat
        if heat and self.on_since is None:
            assert complete
            self._check_start(now, controls, open_valve=open_valve)
            self.starts.append(now)
            self.on_since = now
            self.run_gaps = {now: _gaps(result)}
        elif heat:
            assert self.on_since is not None
            assert now - self.on_since < GUARD.max_run
            self._check_progress(now, self.on_since, result)
            self.run_gaps[now] = _gaps(result)
        elif self.on_since is not None:
            self.last_stop = now
            self.on_since = None
            if result.reason.code in {ReasonCode.MAX_RUN, ReasonCode.NO_PROGRESS}:
                self.lockout_end = now + GUARD.lockout

    def _check_start(self, now: datetime, controls: Controls, *, open_valve: bool) -> None:
        recent = [start for start in self.starts if now - start < GUARD.start_window]
        assert len(recent) < GUARD.max_starts
        assert self.last_stop is None or now - self.last_stop >= GUARD.min_off
        assert self.lockout_end is None or now >= self.lockout_end
        assert not controls.paused
        assert open_valve

    def _check_progress(self, now: datetime, on_since: datetime, result: Evaluation) -> None:
        """A run past the check must have a room, served a window ago, that progressed."""
        earlier = self.run_gaps.get(now - GUARD.progress_window)
        if now - on_since < GUARD.progress_check_after or not earlier:
            return
        current = _gaps(result)
        satisfied = {room.name for room in result.rooms if room.status is RoomStatus.SATISFIED}
        assert any(
            name in satisfied
            or (name in current and current[name] <= before - GUARD.progress_margin_k)
            for name, before in earlier.items()
        )


def _gaps(result: Evaluation) -> dict[str, float]:
    """The served rooms' gaps, rounded as the guard stores them."""
    return {
        room.name: round(room.error_k, GUARD.progress_sample_decimals)
        for room in result.rooms
        if room.served and room.error_k is not None
    }


def _chance(rng: random.Random, probability: float) -> bool:
    return rng.random() < probability


# Heat loss per minute: mild weather, cold weather in which heating barely gains, and a
# cold snap in which an open valve cannot keep up (a run that makes no progress).
LOSSES_K = (0.01, 0.035, 0.045)


@dataclass(frozen=True)
class Regime:
    """
    How noisy a seed's house is, per minute.

    A flaky board switches off often enough for restarts to run into the start limit; a
    steady house lets runs last long enough to reach the absolute run limit.
    """

    board_off: float = 0.005
    board_unavailable: float = 0.002
    target_change: float = 0.005


REGIMES = (
    Regime(),
    Regime(board_off=0.02),
    Regime(board_off=0.0, board_unavailable=0.0, target_change=0.0005),
)


def _move_rooms(
    rng: random.Random, rooms: list[SimRoom], regime: Regime, *, heating: bool, loss_k: float
) -> None:
    for sim in rooms:
        gain = 0.04 if heating and sim.valve_open else 0.0
        sim.wall += gain - loss_k + rng.uniform(-0.01, 0.01)
        sim.valve_open = sim.wall < sim.target - 0.2 if _chance(rng, 0.9) else sim.valve_open
        if _chance(rng, regime.target_change):
            sim.target = rng.choice([17.0, 19.0, 21.0, 22.0])
        if _chance(rng, 0.003):
            sim.window_open = not sim.window_open
        if _chance(rng, 0.002):
            sim.wall_dead = not sim.wall_dead


def _observed_boiler(rng: random.Random, state: EngineState, regime: Regime) -> bool | None:
    """Mostly what the engine commanded; sometimes unavailable or switched off by the board."""
    if _chance(rng, regime.board_unavailable):
        return None
    if _chance(rng, regime.board_off):
        return False
    return state.boiler_on


def _random_controls(rng: random.Random, paused: bool) -> Controls:  # noqa: FBT001 - helper
    return Controls(
        paused=paused,
        clear_lockout=_chance(rng, 0.002),
    )


@pytest.mark.parametrize("seed", range(9))
def test_limits_hold_over_two_noisy_days(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - reproducible test data, not cryptography
    regime = REGIMES[seed % len(REGIMES)]
    rooms = [
        SimRoom("bedroom", 16.0, sonoff=False, wall=19.0),
        SimRoom("kitchen", 5.5, sonoff=False, wall=20.0),
        SimRoom("living_room", 18.0, sonoff=True, wall=20.5),
    ]
    state = EngineState()
    history = History()
    paused = False
    loss_k = LOSSES_K[0]
    now = NOW
    seen: set[ReasonCode] = set()
    for _ in range(STEPS):
        if _chance(rng, 0.002):
            paused = not paused
        if _chance(rng, 0.003):
            loss_k = rng.choice(LOSSES_K)
        controls = _random_controls(rng, paused)
        if controls.clear_lockout:
            history.lockout_end = None
        snap = snapshot(
            *(sim.snapshot(now) for sim in rooms),
            now=now,
            outdoor=rng.choice([None, -5.0, 0.0, 5.0, 12.0]),
            boiler_on=_observed_boiler(rng, state, regime),
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
        history.observe(
            now,
            result,
            controls,
            complete=snap.complete,
            open_valve=any(sim.valve_open for sim in rooms),
        )

        _move_rooms(rng, rooms, regime, heating=heat, loss_k=loss_k)
        now += STEP

    assert history.starts, "the scenario should exercise at least one run"
    SEEN.update(seen)


SEEN: set[ReasonCode] = set()


def test_random_runs_reach_every_decision() -> None:
    # runs after the parametrized scenarios above (pytest keeps file order)
    assert set(ReasonCode) == SEEN
