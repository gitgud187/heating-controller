from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.heating_controller.engine.model import ThermostatMode
from sim.devices import (
    Board,
    BoardParams,
    FailsafeCause,
    Schedule,
    Sensor,
    SensorFault,
    SensorParams,
    ThermostatSim,
    ValveFault,
    ValveKind,
    ValveParams,
    ValveSim,
    quantise,
)

T0 = datetime(2026, 1, 14, 12, 0, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def quiet() -> random.Random:
    return random.Random(0)  # noqa: S311 - test noise


def silent(params: SensorParams) -> SensorParams:
    return SensorParams(
        interval=params.interval,
        change_k=params.change_k,
        min_interval=params.min_interval,
        noise_k=0.0,
        resolution_k=params.resolution_k,
        offset_k=params.offset_k,
    )


def test_quantise() -> None:
    assert quantise(20.04, 0.1) == 20.0
    assert quantise(20.06, 0.1) == 20.1


def test_sensor_reports_periodically() -> None:
    sensor = Sensor(silent(SensorParams(interval=5 * MINUTE)), start=T0, phase=timedelta())
    assert sensor.update(T0, 20.0, quiet())
    assert not sensor.update(T0 + MINUTE, 20.0, quiet())
    assert sensor.reading().last_reported == T0
    assert sensor.update(T0 + 5 * MINUTE, 20.0, quiet())
    assert sensor.reading().value == 20.0
    assert sensor.reading().last_reported == T0 + 5 * MINUTE


def test_sensor_reports_on_change() -> None:
    params = silent(SensorParams(interval=5 * MINUTE, change_k=0.2, offset_k=0.5))
    sensor = Sensor(params, start=T0, phase=timedelta())
    sensor.update(T0, 20.0, quiet())
    assert sensor.value == 20.5
    assert not sensor.update(T0 + MINUTE, 20.1, quiet())
    assert sensor.update(T0 + 2 * MINUTE, 20.2, quiet())
    assert sensor.value == 20.7


def test_dead_sensor_goes_unavailable_once() -> None:
    sensor = Sensor(silent(SensorParams()), start=T0, phase=timedelta())
    sensor.update(T0, 20.0, quiet())
    sensor.fault = SensorFault.DEAD
    assert sensor.update(T0 + MINUTE, 20.0, quiet())
    assert sensor.reading().value is None
    assert not sensor.update(T0 + 2 * MINUTE, 20.0, quiet())


def test_frozen_sensor_keeps_value_and_timestamp() -> None:
    sensor = Sensor(silent(SensorParams(interval=MINUTE)), start=T0, phase=timedelta())
    sensor.update(T0, 20.0, quiet())
    sensor.fault = SensorFault.FROZEN
    assert not sensor.update(T0 + 30 * MINUTE, 25.0, quiet())
    assert sensor.reading().value == 20.0
    assert sensor.reading().last_reported == T0


def moes() -> ValveSim:
    return ValveSim(ValveParams(kind=ValveKind.PROPORTIONAL, noise_k=0.0), start=T0)


def test_moes_opens_proportionally_in_steps_and_reports_on_change() -> None:
    valve = moes()
    valve.update(T0, 20.5, 21.0, quiet())
    assert valve.position_pct % 5 == 0
    assert 0 < valve.opening < 1
    assert valve.heating
    assert valve.position_reported == T0
    assert valve.heating_reported == T0
    valve.update(T0 + 30 * timedelta(seconds=1), 18.0, 21.0, quiet())  # not yet sampled
    assert valve.position_reported == T0


def test_moes_integral_closes_the_remaining_error() -> None:
    valve = moes()
    openings = []
    for minute in range(120):
        valve.update(T0 + minute * MINUTE, 20.9, 21.0, quiet())
        openings.append(valve.opening)
    # 0.1 K short: P alone opens 10 %; the integral keeps opening
    assert openings[0] == pytest.approx(0.1)
    assert openings[-1] > 0.3


def test_moes_closes_above_target_and_reports_idle() -> None:
    valve = moes()
    valve.update(T0, 20.0, 21.0, quiet())
    valve.update(T0 + MINUTE, 22.5, 21.0, quiet())
    assert valve.opening == 0.0
    assert not valve.heating
    assert valve.heating_reported == T0 + MINUTE


def sonoff() -> ValveSim:
    return ValveSim(ValveParams(kind=ValveKind.ON_OFF, noise_k=0.0), start=T0)


def test_sonoff_switches_with_hysteresis() -> None:
    valve = sonoff()
    temps = [20.9, 20.8, 20.9, 21.0, 20.9]
    openings = []
    for minute, temp in enumerate(temps):
        valve.update(T0 + minute * MINUTE, temp, 21.0, quiet())
        openings.append(valve.opening)
    assert openings == [0.0, 1.0, 1.0, 0.0, 0.0]


def test_thermostat_holding_the_valve_off_closes_it() -> None:
    valve = sonoff()
    valve.update(T0, 15.0, None, quiet())
    assert valve.opening == 0.0


def test_stuck_open_valve_reports_heating() -> None:
    for valve in (moes(), sonoff()):
        valve.fault = ValveFault.STUCK_OPEN
        valve.update(T0, 25.0, 21.0, quiet())
        assert valve.opening == 1.0
        view = valve.engine_view()
        assert view.heating is not None
        assert view.heating.value is True


def test_valve_that_never_opens_reports_closed() -> None:
    valve = moes()
    valve.fault = ValveFault.NEVER_OPENS
    valve.update(T0, 15.0, 21.0, quiet())
    view = valve.engine_view()
    assert view.position is not None
    assert view.position.value == 0.0
    assert view.heating is not None
    assert view.heating.value is False


def test_unavailable_valve_has_no_data() -> None:
    for valve in (moes(), sonoff()):
        valve.fault = ValveFault.UNAVAILABLE
        view = valve.engine_view()
        assert view.heating is not None
        assert view.heating.value is None
        if view.position is not None:
            assert view.position.value is None


def test_on_off_valve_reports_no_position() -> None:
    assert sonoff().engine_view().position is None


def test_schedule_and_overrides() -> None:
    away = (T0, T0 + timedelta(hours=2), 16.0)
    schedule = Schedule(points=((6.0, 21.0), (22.0, 18.0)), overrides=(away,))
    assert schedule.target_at(T0.replace(hour=3)) == 18.0
    assert schedule.target_at(T0.replace(hour=7)) == 21.0
    assert schedule.target_at(T0.replace(hour=23)) == 18.0
    assert schedule.target_at(T0 + MINUTE) == 16.0


def test_thermostat_reports_with_the_wall_sensor_and_on_change() -> None:
    thermostat = ThermostatSim(Schedule(points=((6.0, 21.0), (22.0, 18.0))), target_c=21.0)
    thermostat.update(T0, wall_reported=False)
    assert thermostat.last_reported == datetime.min.replace(tzinfo=UTC)
    thermostat.update(T0, wall_reported=True)
    assert thermostat.engine_view().target.last_reported == T0
    late = T0.replace(hour=22)
    thermostat.update(late, wall_reported=False)
    assert thermostat.target_c == 18.0
    assert thermostat.last_reported == late


@pytest.mark.parametrize(
    ("mode", "window", "expected"),
    [
        (ThermostatMode.HEAT, False, 21.0),
        (ThermostatMode.OFF, False, None),
        (ThermostatMode.HEAT, True, None),
    ],
)
def test_thermostat_valve_target(
    mode: ThermostatMode,
    window: bool,  # noqa: FBT001 - parametrized
    expected: float | None,
) -> None:
    thermostat = ThermostatSim(
        Schedule(points=((0.0, 21.0),)), mode=mode, window_open=window, target_c=21.0
    )
    assert thermostat.valve_target() == expected
    assert thermostat.engine_view().window_open is window


def test_board_heartbeat_failsafe() -> None:
    board = Board(BoardParams(), start=T0)
    assert not board.heating
    board.write(heat=True, power_pct=60)
    board.press_heartbeat(T0)
    assert board.tick(T0 + 4 * MINUTE) is None
    assert board.tick(T0 + 5 * MINUTE) is FailsafeCause.HEARTBEAT
    assert not board.heating
    assert board.failsafe is FailsafeCause.HEARTBEAT
    assert board.tick(T0 + 6 * MINUTE) is None
    board.write(heat=True, power_pct=60)
    assert board.failsafe is None


def test_board_api_failsafe() -> None:
    board = Board(BoardParams(), start=T0)
    board.write(heat=True, power_pct=60)
    board.set_api(T0, connected=False)
    board.set_api(T0 + MINUTE, connected=False)
    board.press_heartbeat(T0 + 4 * MINUTE)
    assert board.tick(T0 + 5 * MINUTE) is FailsafeCause.API
    board.set_api(T0 + 6 * MINUTE, connected=True)
    assert board.api_lost_since is None
