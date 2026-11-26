"""The single entry point: snapshot + state → command + new state + reasons."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .boiler import CoreInput, decide, total_load, update_starvation
from .demand import RoomAssessment, RoomState, RoomStatus, assess_room
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
    state, external_stop = _observe_boiler(snapshot, state, config)
    rooms = tuple(
        assess_room(room, state.rooms.get(room.name, RoomState()), now, config)
        for room in snapshot.rooms
    )
    load = total_load(rooms)
    all_excluded = all(room.excluded for room in rooms)
    excluded_since = (state.excluded_since or now) if all_excluded else None
    starving = update_starvation(
        state.starving_since, rooms, boiler_on=state.boiler_on, load_m2k=load, now=now
    )
    core = decide(
        CoreInput(
            now=now,
            on_since=state.on_since,
            load_m2k=load,
            starving_since=starving,
            all_excluded=all_excluded,
            excluded_since=excluded_since,
        ),
        config.boiler,
    )
    holding = core.reason.code is ReasonCode.RUNNING_EXCLUDED_HOLD
    modulation = modulate(
        ModulationInput(
            now=now,
            rooms=rooms,
            outdoor_c=fresh_value(snapshot.outdoor, now, config.validity.outdoor_stale_after),
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
            gaps_k={
                room.name: room.error_k
                for room in rooms
                if room.served and room.error_k is not None
            },
            satisfied=frozenset(room.name for room in rooms if room.status is RoomStatus.SATISFIED),
            on_since=state.on_since,
            progress=state.progress,
            last_off=state.last_off,
            starts=state.starts,
            lockout_until=state.lockout_until,
            no_open_valve_since=state.no_open_valve_since,
            # While holding without trustworthy data the valves are unknown, not closed.
            any_valve_open=holding or any(room.valve.open for room in rooms),
            snapshot_complete=snapshot.complete,
            snapshot_age=now - snapshot.taken_at,
            paused=controls.paused,
            clear_lockout=controls.clear_lockout,
        ),
        config.guard,
    )

    reasons = (core.reason,) if guarded.reason is None else (core.reason, guarded.reason)
    if external_stop:
        reasons = (
            Reason(
                ReasonCode.EXTERNAL_STOP, "off: the boiler was switched off outside the controller"
            ),
            *reasons,
        )
    command: Command | None = Command(heat=guarded.heat, modulation_pct=guarded.modulation_pct)
    if snapshot.boiler_on is None:
        command = None
        reasons = (
            *reasons,
            Reason(ReasonCode.BOILER_UNAVAILABLE, "off: boiler unavailable, nothing commanded"),
        )

    new_state = _next_state(
        state,
        excluded_since=excluded_since,
        now=now,
        command=command,
        guarded=guarded,
        rooms=rooms,
        starving=starving,
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


def _observe_boiler(
    snapshot: Snapshot, state: EngineState, config: EngineConfig
) -> tuple[EngineState, bool]:
    """
    Accept a stop made outside the engine.

    The board switches the boiler off on its own when the heartbeat or the API goes away. If
    the engine believed the boiler on, the next start would otherwise not count as a start
    and would skip the minimum off time. The stop is dated now (its real time is unknown),
    which only lengthens the minimum off. A boiler seen off right after a start, before the
    switch could report, is not a stop.
    """
    on_since = state.on_since
    if (
        snapshot.boiler_on is not False
        or on_since is None
        or snapshot.now - on_since < config.boiler.external_stop_grace
    ):
        return state, False
    stopped = replace(
        state, on_since=None, last_off=snapshot.now, no_open_valve_since=None, progress=()
    )
    return stopped, True


def _next_state(  # noqa: PLR0913 - assembles every part of the new state
    state: EngineState,
    *,
    now: datetime,
    command: Command | None,
    guarded: GuardResult,
    rooms: tuple[RoomAssessment, ...],
    starving: dict[str, datetime],
    modulation: ModulationResult,
    config: EngineConfig,
    excluded_since: datetime | None,
) -> EngineState:
    heat = command is not None and command.heat
    starts = recent_starts(state.starts, now, config.guard.start_window)
    if heat and not state.boiler_on:
        starts = (*starts, now)
    on_since = (state.on_since or now) if heat else None
    return EngineState(
        on_since=on_since,
        last_off=now if state.boiler_on and not heat else state.last_off,
        starts=starts,
        lockout_until=guarded.lockout_until,
        no_open_valve_since=guarded.no_open_valve_since if heat else None,
        progress=guarded.progress if heat else (),
        integral_pct=modulation.integral_pct,
        modulation_pct=None if command is None else command.modulation_pct,
        last_evaluated=now,
        rooms={room.name: room.state for room in rooms},
        starving_since={} if heat else starving,
        excluded_since=excluded_since,
    )
