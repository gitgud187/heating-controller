from __future__ import annotations

import math
from datetime import timedelta

import pytest

from custom_components.heating_controller.engine.config import EngineConfig, ModulationConfig
from custom_components.heating_controller.engine.demand import (
    RoomAssessment,
    RoomState,
    RoomStatus,
    assess_room,
)
from custom_components.heating_controller.engine.model import ThermostatMode
from custom_components.heating_controller.engine.modulation import (
    ModulationInput,
    ModulationResult,
    base_pct,
    modulate,
    update_integral,
    weighted_error,
)

from .factories import NOW, moes, room

CONFIG = ModulationConfig()


def served(name: str, area_m2: float, wall: float, target: float = 21.0) -> RoomAssessment:
    return assess_room(
        room(name, area_m2=area_m2, wall=wall, target=target), RoomState(), NOW, EngineConfig()
    )


def step(  # noqa: PLR0913 - a builder is clearer with every knob as a keyword
    *,
    rooms: tuple[RoomAssessment, ...] = (),
    outdoor_c: float | None = 10.0,
    integral_pct: float = 0.0,
    since_last: timedelta | None = timedelta(minutes=1),
    on_for: timedelta | None = None,
    last_pct: int | None = None,
    starting: bool = False,
) -> ModulationResult:
    return modulate(
        ModulationInput(
            now=NOW,
            rooms=rooms,
            outdoor_c=outdoor_c,
            integral_pct=integral_pct,
            last_evaluated=None if since_last is None else NOW - since_last,
            on_since=None if on_for is None else NOW - on_for,
            last_pct=last_pct,
            starting=starting,
        ),
        CONFIG,
    )


@pytest.mark.parametrize(
    ("outdoor_c", "expected"),
    [(15.0, 30.0), (10.0, 30.0), (2.5, 50.0), (0.0, 30.0 + 10 * 40 / 15), (-5.0, 70.0), (-20, 70)],
)
def test_heating_curve(outdoor_c: float, expected: float) -> None:
    assert base_pct(outdoor_c, CONFIG) == pytest.approx(expected)


FLOOR = CONFIG.satisfied_floor_k


def test_weighted_error_is_area_weighted_over_served_and_satisfied_rooms() -> None:
    rooms = [
        served("big", 15.0, wall=20.0),
        served("small", 5.0, wall=19.0),
        served("warm", 20.0, wall=21.3),
    ]
    assert weighted_error(rooms, FLOOR) == pytest.approx((15 * 1.0 + 5 * 2.0 - 20 * 0.3) / 40)


def test_weighted_error_turns_negative_when_rooms_overshoot() -> None:
    # The served-only error could never go below 0, so the integral only ever grew.
    rooms = [served("cold", 10.0, wall=20.9), served("warm", 10.0, wall=21.4)]
    assert weighted_error(rooms, FLOOR) < 0


def test_satisfied_room_counts_no_lower_than_the_floor() -> None:
    rooms = [served("setback", 10.0, wall=24.0)]
    assert weighted_error(rooms, FLOOR) == pytest.approx(FLOOR)


def test_weighted_error_ignores_rooms_that_take_no_heat() -> None:
    off = assess_room(
        room("off", wall=15.0, mode=ThermostatMode.OFF), RoomState(), NOW, EngineConfig()
    )
    assert weighted_error([off], FLOOR) == 0.0
    assert weighted_error([], FLOOR) == 0.0


def integral(
    *,
    value: float = 0.0,
    error_k: float = 1.0,
    proportional_pct: float = 50.0,
    dt: timedelta = timedelta(minutes=1),
    running_for: timedelta | None = timedelta(minutes=30),
) -> float:
    return update_integral(
        integral_pct=value,
        error_k=error_k,
        proportional_pct=proportional_pct,
        dt=dt,
        running_for=running_for,
        config=CONFIG,
    )


def test_integral_grows_while_running() -> None:
    assert integral(dt=timedelta(minutes=3)) == pytest.approx(0.5)


def test_integral_step_is_capped() -> None:
    assert integral(dt=timedelta(hours=2)) == pytest.approx(10 * 5 / 60)


def test_integral_is_bounded() -> None:
    assert integral(value=29.9, dt=timedelta(minutes=5)) == 30.0
    assert integral(value=-29.9, error_k=-1.0, proportional_pct=70.0) == -30.0


def test_integral_frozen_while_a_valve_is_awaited() -> None:
    held = update_integral(
        integral_pct=5.0,
        error_k=1.0,
        proportional_pct=50.0,
        dt=timedelta(minutes=3),
        running_for=timedelta(minutes=30),
        config=CONFIG,
        hold=True,
    )
    assert held == 5.0


def test_modulation_holds_the_integral_while_a_room_waits_for_its_valve() -> None:
    waiting = assess_room(
        room("a", wall=20.0, valve=moes(heating=False, position=0.0)),
        RoomState(),
        NOW,
        EngineConfig(),
    )
    assert waiting.status is RoomStatus.WAITING_FOR_VALVE
    result = step(
        rooms=(waiting,),
        integral_pct=4.0,
        on_for=timedelta(minutes=30),
        since_last=timedelta(minutes=3),
    )
    assert result.integral_pct == 4.0
    assert result.p_pct == pytest.approx(15.0)


def test_integral_unwinds_while_rooms_sit_above_target() -> None:
    rooms = (served("warm", 10.0, wall=21.3),)
    result = step(
        rooms=rooms,
        integral_pct=20.0,
        on_for=timedelta(minutes=30),
        since_last=timedelta(minutes=3),
    )
    assert result.integral_pct == pytest.approx(20.0 - 10 * 0.3 * 3 / 60)


def test_integral_frozen_in_first_minutes_of_run() -> None:
    assert integral(value=5.0, running_for=timedelta(minutes=9, seconds=59)) == 5.0
    assert integral(value=5.0, running_for=timedelta(minutes=10)) > 5.0


@pytest.mark.parametrize(("proportional_pct", "value"), [(25.0, 5.0), (95.0, 5.0), (40.0, -10.0)])
def test_integral_frozen_while_saturated(proportional_pct: float, value: float) -> None:
    assert integral(value=value, proportional_pct=proportional_pct) == value


def test_integral_decays_while_off() -> None:
    decayed = integral(value=20.0, running_for=None, dt=timedelta(hours=6))
    assert decayed == pytest.approx(20.0 / math.e)


def test_modulation_combines_base_p_and_i() -> None:
    result = step(rooms=(served("a", 10.0, wall=20.0),), outdoor_c=2.5, integral_pct=4.0)
    assert result.base_pct == pytest.approx(50.0)
    assert result.error_k == pytest.approx(1.0)
    assert result.p_pct == pytest.approx(15.0)
    assert result.integral_pct == pytest.approx(4.0 * math.exp(-1 / 360))
    assert result.target_pct == 69
    assert result.output_pct == 69
    assert not result.outdoor_fallback


def test_invalid_outdoor_uses_the_base_for_zero_degrees() -> None:
    result = step(outdoor_c=None)
    assert result.outdoor_fallback
    assert result.base_pct == pytest.approx(base_pct(0.0, CONFIG))


def test_output_is_clamped() -> None:
    assert step(rooms=(served("a", 10.0, wall=15.0),), outdoor_c=-10.0).target_pct == 100


def test_rounding_is_half_up() -> None:
    # base 30 + P 15 * 0.5 = 37.5
    result = step(rooms=(served("a", 10.0, wall=20.5),))
    assert result.target_pct == 38


def test_small_changes_are_not_written() -> None:
    rooms = (served("a", 10.0, wall=20.0),)  # target 45
    assert step(rooms=rooms, last_pct=44).output_pct == 44
    assert step(rooms=rooms, last_pct=43).output_pct == 45
    assert step(rooms=rooms, last_pct=44, starting=True).output_pct == 45


def test_first_evaluation_has_no_interval() -> None:
    result = step(integral_pct=6.0, since_last=None)
    assert result.integral_pct == 6.0


def test_clock_going_backwards_is_no_interval() -> None:
    result = step(integral_pct=6.0, since_last=-timedelta(minutes=5))
    assert result.integral_pct == 6.0


def test_integral_runs_with_the_previous_boiler_state() -> None:
    rooms = (served("a", 10.0, wall=20.0),)
    result = step(rooms=rooms, on_for=timedelta(minutes=20), since_last=timedelta(minutes=3))
    assert result.integral_pct == pytest.approx(0.5)
