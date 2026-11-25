"""The single entry point: snapshot + state → command + new state + reasons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .boiler import CoreInput, decide, total_load, update_starvation
from .demand import RoomAssessment, RoomState, assess_room
from .guard import GuardInput, GuardResult, apply_guard, recent_starts
from .modulation import ModulationInput, ModulationResult, modulate
from .reasons import Reason, ReasonCode
from .state import EngineState
from .validity import fresh_value

if TYPE_CHECKING:
    from datetime import datetime

    from .config import EngineConfig
    from .model import Controls, Snapshot


@dataclass(frozen=True, slots=True)
class Command:
    """What the adapter writes: the boiler switch and the output request."""

    heat: bool
    modulation_pct: int


@dataclass(frozen=True, slots=True)
class Evaluation:
    """
    The result of one evaluation.

    `command` is None when the boiler entities are unavailable: nothing is written then.
    `reasons` holds the core's reason, then the guard's or the availability reason if either
    overrode it; the last one is the reason for what actually happens.
    """

    command: Command | None
    state: EngineState
    reasons: tuple[Reason, ...]
    rooms: tuple[RoomAssessment, ...]
    load_m2k: float
    modulation: ModulationResult
    starts_last_hour: int

    @property
    def reason(self) -> Reason:
        """The reason for what actually happens."""
        return self.reasons[-1]


def evaluate(
    snapshot: Snapshot, state: EngineState, config: EngineConfig, controls: Controls
) -> Evaluation:
    """Run room demand, the core state machine, modulation and the guard, in that order."""
    now = snapshot.now
    rooms = tuple(
        assess_room(room, state.rooms.get(room.name, RoomState()), now, config)
        for room in snapshot.rooms
    )
    load = total_load(rooms)
    starving = update_starvation(
        state.starving_since, rooms, boiler_on=state.boiler_on, load_m2k=load, now=now
    )
    run_now_until = (
        now + config.boiler.run_now_duration if controls.run_now else state.run_now_until
    )
    core = decide(
        CoreInput(
            now=now,
            on_since=state.on_since,
            load_m2k=load,
            starving_since=starving,
            run_now_until=run_now_until,
            all_excluded=all(room.excluded for room in rooms),
        ),
        config.boiler,
    )
    modulation = modulate(
        ModulationInput(
            now=now,
            rooms=rooms,
            outdoor_c=fresh_value(snapshot.outdoor, now, config.validity.stale_after),
            integral_pct=state.integral_pct,
            last_evaluated=state.last_evaluated,
            on_since=state.on_since,
            last_pct=state.modulation_pct,
            starting=core.heat and not state.boiler_on,
        ),
        config.modulation,
    )
    guarded = apply_guard(
        GuardInput(
            now=now,
            heat=core.heat,
            modulation_pct=modulation.output_pct,
            on_since=state.on_since,
            last_off=state.last_off,
            starts=state.starts,
            lockout_until=state.lockout_until,
            no_open_valve_since=state.no_open_valve_since,
            any_valve_open=any(room.valve.open for room in rooms),
            snapshot_complete=snapshot.complete,
            snapshot_age=now - snapshot.taken_at,
            paused=controls.paused,
            clear_lockout=controls.clear_lockout,
        ),
        config.guard,
    )

    reasons = (core.reason,) if guarded.reason is None else (core.reason, guarded.reason)
    command: Command | None = Command(heat=guarded.heat, modulation_pct=guarded.modulation_pct)
    if snapshot.boiler_on is None:
        command = None
        reasons = (
            *reasons,
            Reason(ReasonCode.BOILER_UNAVAILABLE, "off: boiler unavailable, nothing commanded"),
        )

    new_state = _next_state(
        state,
        now=now,
        command=command,
        guarded=guarded,
        rooms=rooms,
        starving=starving,
        run_now_until=run_now_until,
        modulation=modulation,
        config=config,
    )
    return Evaluation(
        command=command,
        state=new_state,
        reasons=reasons,
        rooms=rooms,
        load_m2k=load,
        modulation=modulation,
        starts_last_hour=len(new_state.starts),
    )


def _next_state(  # noqa: PLR0913 - assembles every part of the new state
    state: EngineState,
    *,
    now: datetime,
    command: Command | None,
    guarded: GuardResult,
    rooms: tuple[RoomAssessment, ...],
    starving: dict[str, datetime],
    run_now_until: datetime | None,
    modulation: ModulationResult,
    config: EngineConfig,
) -> EngineState:
    heat = command is not None and command.heat
    starts = recent_starts(state.starts, now, config.guard.start_window)
    if heat and not state.boiler_on:
        starts = (*starts, now)
    on_since = (state.on_since or now) if heat else None
    keep_run_now = heat and run_now_until is not None and now < run_now_until
    return EngineState(
        on_since=on_since,
        last_off=now if state.boiler_on and not heat else state.last_off,
        starts=starts,
        lockout_until=guarded.lockout_until,
        no_open_valve_since=guarded.no_open_valve_since if heat else None,
        run_now_until=run_now_until if keep_run_now else None,
        integral_pct=modulation.integral_pct,
        modulation_pct=None if command is None else command.modulation_pct,
        last_evaluated=now,
        rooms={room.name: room.state for room in rooms},
        starving_since={} if heat else starving,
    )
