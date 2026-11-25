from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from sim.house import default_house
from sim.plant import Coupling, OutdoorProfile, RoomParams, ThermalPlant, effective_output_pct

ROOM = RoomParams(
    name="room",
    area_m2=10.0,
    loss_w_per_k=20.0,
    capacity_j_per_k=20.0 * 3600 * 5,
    radiator_w=1000.0,
    radiator_lag=timedelta(minutes=10),
)
STEP = timedelta(seconds=30)


def settle(plant: ThermalPlant, hours: float, **kwargs: object) -> None:
    for _ in range(int(hours * 120)):
        plant.step(STEP, **kwargs)  # type: ignore[arg-type] # keyword pass-through in a test helper


def test_outdoor_profile_is_coldest_at_the_given_hour() -> None:
    profile = OutdoorProfile(mean_c=0.0, amplitude_k=3.0, coldest_hour=5.0)
    day = datetime(2026, 1, 1, tzinfo=UTC)
    assert profile.at(day.replace(hour=5)) == pytest.approx(-3.0)
    assert profile.at(day.replace(hour=17)) == pytest.approx(3.0)
    assert OutdoorProfile(mean_c=4.0).at(day) == 4.0


@pytest.mark.parametrize(
    ("heating", "request_pct", "expected"),
    [
        (False, 80.0, 0.0),
        (True, 1.0, 30.0),
        (True, 29.0, 30.0),
        (True, 55.0, 55.0),
        (True, 120, 100),
    ],
)
def test_boiler_has_a_30_percent_floor(heating: bool, request_pct: float, expected: float) -> None:  # noqa: FBT001 - parametrized
    assert effective_output_pct(heating=heating, request_pct=request_pct) == expected


def test_steady_state_balances_radiator_and_loss() -> None:
    plant = ThermalPlant((ROOM,), (), {"room": 20.0})
    settle(plant, 60, outdoor_c=0.0, output_pct=40.0, openings={"room": 1.0}, windows_open={})
    # 400 W against 20 W/K: 20 K above outdoor
    assert plant.temps_c["room"] == pytest.approx(20.0, abs=0.05)
    assert plant.radiator_w["room"] == pytest.approx(400.0)


def test_radiator_follows_with_a_first_order_lag() -> None:
    plant = ThermalPlant((ROOM,), (), {"room": 20.0})
    for _ in range(20):  # 10 min = one time constant
        plant.step(STEP, outdoor_c=0.0, output_pct=100.0, openings={"room": 1.0}, windows_open={})
    assert plant.radiator_w["room"] == pytest.approx(1000 * (1 - 1 / 2.718281828), rel=0.01)


def test_closed_valve_and_open_window_cool_faster() -> None:
    closed = ThermalPlant((ROOM,), (), {"room": 20.0})
    window = ThermalPlant((ROOM,), (), {"room": 20.0})
    closed.step(STEP, outdoor_c=0.0, output_pct=100.0, openings={}, windows_open={})
    window.step(STEP, outdoor_c=0.0, output_pct=100.0, openings={}, windows_open={"room": True})
    assert window.temps_c["room"] < closed.temps_c["room"] < 20.0


def test_coupling_moves_heat_without_creating_it() -> None:
    other = RoomParams("other", 10.0, 1e-9, ROOM.capacity_j_per_k, 0.0)
    lossless = RoomParams("room", 10.0, 1e-9, ROOM.capacity_j_per_k, 0.0)
    plant = ThermalPlant(
        (lossless, other), (Coupling("room", "other", 50.0),), {"room": 22.0, "other": 18.0}
    )
    plant.step(STEP, outdoor_c=0.0, output_pct=0.0, openings={}, windows_open={})
    assert plant.temps_c["room"] < 22.0
    assert plant.temps_c["room"] + plant.temps_c["other"] == pytest.approx(40.0)


def needed_output_pct(outdoor_c: float, indoor_c: float = 21.0) -> dict[str, float]:
    """Output each room of the default house needs with its valve fully open."""
    return {
        spec.params.name: 100
        * (spec.params.loss_w_per_k * (indoor_c - outdoor_c) - spec.params.internal_gain_w)
        / spec.params.radiator_w
        for spec in default_house().rooms
    }


def test_default_house_needs_about_70_percent_at_minus_5() -> None:
    for need in needed_output_pct(-5.0).values():
        assert 50 <= need <= 72


def test_default_house_needs_less_than_30_percent_when_mild() -> None:
    for need in needed_output_pct(10.0).values():
        assert need < 30


def test_house_lookup_and_room_change() -> None:
    house = default_house()
    assert house.room("kitchen").params.area_m2 == 5.6
    with pytest.raises(KeyError):
        house.room("attic")
    changed = house.with_room(
        "kitchen", lambda spec: replace(spec, params=replace(spec.params, area_m2=6.0))
    )
    assert changed.room("kitchen").params.area_m2 == 6.0
    assert changed.room("bedroom") == house.room("bedroom")
