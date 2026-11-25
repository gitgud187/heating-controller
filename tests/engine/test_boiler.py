from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.heating_controller.engine.boiler import (
    CoreInput,
    decide,
    total_load,
    update_starvation,
)
from custom_components.heating_controller.engine.config import BoilerConfig, EngineConfig
from custom_components.heating_controller.engine.demand import RoomState, assess_room
from custom_components.heating_controller.engine.reasons import (
    ReasonCode,
    clock,
    duration,
    minutes,
)

from .factories import NOW, moes, room

CONFIG = BoilerConfig()


def core(
    *,
    load: float = 0.0,
    on_for: timedelta | None = None,
    starving: dict[str, timedelta] | None = None,
    all_excluded: bool = False,
) -> CoreInput:
    return CoreInput(
        now=NOW,
        on_since=None if on_for is None else NOW - on_for,
        load_m2k=load,
        starving_since={name: NOW - ago for name, ago in (starving or {}).items()},
        all_excluded=all_excluded,
    )


def test_total_load_sums_served_rooms_only() -> None:
    config = EngineConfig()
    served = assess_room(room("a", area_m2=10.0, wall=20.0), RoomState(), NOW, config)
    satisfied = assess_room(room("b", area_m2=5.0, wall=22.0), RoomState(), NOW, config)
    blocked = assess_room(
        room("c", valve=moes(heating=False, position=0.0)),
        RoomState(needs_heat=True, blocked_since=NOW - timedelta(hours=1)),
        NOW,
        config,
    )
    assert total_load([served, satisfied, blocked]) == pytest.approx(10.0)


def test_starvation_clock_starts_and_keeps_running() -> None:
    config = EngineConfig()
    served = assess_room(room("a"), RoomState(), NOW, config)
    satisfied = assess_room(room("b", wall=22.0), RoomState(), NOW, config)
    earlier = NOW - timedelta(minutes=30)
    clocks = update_starvation(
        {"a": earlier, "b": earlier}, [served, satisfied], boiler_on=False, load_m2k=1.0, now=NOW
    )
    assert clocks == {"a": earlier}
    assert update_starvation({}, [served], boiler_on=False, load_m2k=1.0, now=NOW) == {"a": NOW}


@pytest.mark.parametrize(("boiler_on", "load"), [(True, 1.0), (False, 0.0)])
def test_starvation_clock_resets(
    boiler_on: bool,  # noqa: FBT001 - parametrized
    load: float,
) -> None:
    served = assess_room(room("a"), RoomState(), NOW, EngineConfig())
    clocks = update_starvation(
        {"a": NOW - timedelta(hours=1)}, [served], boiler_on=boiler_on, load_m2k=load, now=NOW
    )
    assert clocks == {}


def test_off_starts_at_start_load() -> None:
    decision = decide(core(load=4.0), CONFIG)
    assert decision.heat
    assert decision.reason.code is ReasonCode.START_LOAD
    assert decision.reason.message == "start: load 4.0 ≥ 4.0 m²·K"


def test_off_waits_below_start_load() -> None:
    decision = decide(core(load=3.9, starving={"bedroom": timedelta(minutes=45)}), CONFIG)
    assert not decision.heat
    assert decision.reason.code is ReasonCode.WAITING_LOAD
    assert decision.reason.message == "waiting: load 3.9 < 4.0 m²·K"


def test_off_idle_without_load() -> None:
    decision = decide(core(load=0.0, starving={"bedroom": timedelta(hours=2)}), CONFIG)
    assert not decision.heat
    assert decision.reason.code is ReasonCode.IDLE


def test_starvation_start_after_45_min() -> None:
    decision = decide(
        core(
            load=1.0,
            starving={"kitchen": timedelta(minutes=50), "bedroom": timedelta(minutes=46)},
        ),
        CONFIG,
    )
    assert decision.heat
    assert decision.reason.code is ReasonCode.START_STARVATION
    assert decision.reason.message == "start: kitchen has needed heat for 50 min (load 1.0 m²·K)"


def test_on_keeps_running_above_stop_load() -> None:
    decision = decide(core(load=1.5, on_for=timedelta(hours=1)), CONFIG)
    assert decision.heat
    assert decision.reason.code is ReasonCode.RUNNING_LOAD


def test_on_holds_for_minimum_on_time() -> None:
    decision = decide(core(load=0.0, on_for=timedelta(minutes=6, seconds=30)), CONFIG)
    assert decision.heat
    assert decision.reason.code is ReasonCode.RUNNING_MIN_ON
    assert decision.reason.message.endswith("minimum on 4 min left")


def test_on_stops_below_stop_load_after_minimum_on() -> None:
    decision = decide(core(load=1.4, on_for=timedelta(minutes=10)), CONFIG)
    assert not decision.heat
    assert decision.reason.code is ReasonCode.STOP_LOAD
    assert decision.reason.message == "stopped: load 1.4 < 1.5 m²·K after 10 min"


def test_all_rooms_excluded_wins_over_everything() -> None:
    decision = decide(
        core(
            load=10.0,
            on_for=timedelta(minutes=1),
            all_excluded=True,
        ),
        CONFIG,
    )
    assert not decision.heat
    assert decision.reason.code is ReasonCode.ALL_ROOMS_EXCLUDED


def test_reason_helpers() -> None:
    assert duration(timedelta(minutes=45)) == "45 min"
    assert duration(timedelta(hours=3)) == "3 h"
    assert duration(timedelta(hours=3, minutes=11, seconds=1)) == "3 h 12 min"
    assert minutes(timedelta(seconds=61)) == 2
    assert minutes(timedelta(seconds=-5)) == 0
    assert clock(NOW) == "12:00"
