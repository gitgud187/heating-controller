from __future__ import annotations

import math
from datetime import timedelta

import pytest

from custom_components.heating_controller.engine.config import EngineConfig, ModulationConfig
from custom_components.heating_controller.engine.demand import (
    RoomAssessment,
    RoomState,
    assess_room,
)
from custom_components.heating_controller.engine.modulation import (
    ModulationInput,
    ModulationResult,
    base_pct,
    modulate,
    update_integral,
    weighted_error,
)

from .factories import NOW, room

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


def test_weighted_error_is_area_weighted_over_served_rooms() -> None:
    rooms = [
        served("big", 15.0, wall=20.0),
        served("small", 5.0, wall=19.0),
        served("warm", 50.0, wall=22.0),
    ]
    assert weighted_error(rooms) == pytest.approx((15 * 1.0 + 5 * 2.0) / 20)


def test_weighted_error_without_served_rooms_is_zero() -> None:
    assert weighted_error([served("warm", 10.0, wall=22.0)]) == 0.0


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
