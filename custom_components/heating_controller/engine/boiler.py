"""Load and the core boiler state machine (spec 7). The guard runs afterwards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .reasons import Reason, ReasonCode, minutes

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import datetime

    from .config import BoilerConfig
    from .demand import RoomAssessment


@dataclass(frozen=True, slots=True)
class CoreInput:
    """What the state machine looks at. `on_since` is None while the boiler is off."""

    now: datetime
    on_since: datetime | None
    load_m2k: float
    starving_since: Mapping[str, datetime]
    all_excluded: bool


@dataclass(frozen=True, slots=True)
class CoreDecision:
    """What the core wants, before the guard."""

    heat: bool
    reason: Reason


def total_load(rooms: Iterable[RoomAssessment]) -> float:
    """Area-weighted load over served rooms, in m²·K."""
    return sum(room.load_m2k for room in rooms)


def update_starvation(
    previous: Mapping[str, datetime],
    rooms: Iterable[RoomAssessment],
    *,
    boiler_on: bool,
    load_m2k: float,
    now: datetime,
) -> dict[str, datetime]:
    """
    Per served room, since when it has needed heat while the boiler was off.

    The clocks only run while the boiler is off; a run and a load of 0 reset them all.
    """
    if boiler_on or load_m2k <= 0:
        return {}
    return {room.name: previous.get(room.name, now) for room in rooms if room.served}


def decide(core: CoreInput, config: BoilerConfig) -> CoreDecision:
    """Start, stop or hold, with the reason."""
    if core.all_excluded:
        return CoreDecision(
            heat=False,
            reason=Reason(ReasonCode.ALL_ROOMS_EXCLUDED, "off: no room has trustworthy data"),
        )
    if core.on_since is None:
        return _decide_off(core, config)
    return _decide_on(core, core.on_since, config)


def _decide_off(core: CoreInput, config: BoilerConfig) -> CoreDecision:
    load, start = core.load_m2k, config.start_load_m2k
    if load >= start:
        return CoreDecision(
            heat=True,
            reason=Reason(ReasonCode.START_LOAD, f"start: load {load:.1f} ≥ {start:.1f} m²·K"),
        )
    if load <= 0:
        return CoreDecision(
            heat=False, reason=Reason(ReasonCode.IDLE, "off: no served room needs heat")
        )
    room, since = min(core.starving_since.items(), key=lambda item: item[1], default=("", None))
    if since is not None and core.now - since > config.starvation_after:
        waited = minutes(core.now - since)
        return CoreDecision(
            heat=True,
            reason=Reason(
                ReasonCode.START_STARVATION,
                f"start: {room} has needed heat for {waited} min (load {load:.1f} m²·K)",
            ),
        )
    return CoreDecision(
        heat=False,
        reason=Reason(ReasonCode.WAITING_LOAD, f"waiting: load {load:.1f} < {start:.1f} m²·K"),
    )


def _decide_on(core: CoreInput, on_since: datetime, config: BoilerConfig) -> CoreDecision:
    load, stop = core.load_m2k, config.stop_load_m2k
    if load >= stop:
        return CoreDecision(
            heat=True,
            reason=Reason(ReasonCode.RUNNING_LOAD, f"running: load {load:.1f} ≥ {stop:.1f} m²·K"),
        )
    ran = core.now - on_since
    if ran < config.min_on:
        left = minutes(config.min_on - ran)
        return CoreDecision(
            heat=True,
            reason=Reason(
                ReasonCode.RUNNING_MIN_ON,
                f"running: load {load:.1f} < {stop:.1f} m²·K, minimum on {left} min left",
            ),
        )
    return CoreDecision(
        heat=False,
        reason=Reason(
            ReasonCode.STOP_LOAD,
            f"stopped: load {load:.1f} < {stop:.1f} m²·K after {minutes(ran)} min",
        ),
    )
