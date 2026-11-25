from __future__ import annotations

import json
from datetime import timedelta

import pytest

from custom_components.heating_controller.engine.demand import RoomState
from custom_components.heating_controller.engine.state import EngineState

from .factories import NOW

FULL = EngineState(
    on_since=NOW - timedelta(minutes=20),
    last_off=NOW - timedelta(hours=1),
    starts=(NOW - timedelta(minutes=50), NOW - timedelta(minutes=20)),
    lockout_until=NOW + timedelta(minutes=10),
    no_open_valve_since=NOW - timedelta(minutes=1),
    run_now_until=NOW + timedelta(minutes=5),
    integral_pct=4.25,
    modulation_pct=55,
    last_evaluated=NOW,
    rooms={
        "bedroom": RoomState(needs_heat=True, blocked_since=NOW - timedelta(minutes=3)),
        "kitchen": RoomState(),
    },
    starving_since={"bedroom": NOW - timedelta(minutes=10)},
)


def test_default_state_is_off() -> None:
    assert not EngineState().boiler_on
    assert FULL.boiler_on


@pytest.mark.parametrize("state", [EngineState(), FULL])
def test_round_trip_through_json(state: EngineState) -> None:
    data = json.loads(json.dumps(state.to_dict()))
    assert EngineState.from_dict(data) == state


def test_integer_integral_is_accepted() -> None:
    assert EngineState.from_dict({"version": 1, "integral_pct": 3}).integral_pct == 3.0


def test_missing_keys_take_defaults() -> None:
    assert EngineState.from_dict({"version": 1}) == EngineState()


def test_room_without_fields_takes_defaults() -> None:
    state = EngineState.from_dict({"version": 1, "rooms": {"bedroom": {}}})
    assert state.rooms == {"bedroom": RoomState()}


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 2},
        {"version": None},
        {"on_since": 5},
        {"starts": "2026-11-25T12:00:00+00:00"},
        {"integral_pct": "1.0"},
        {"integral_pct": True},
        {"modulation_pct": 5.5},
        {"modulation_pct": False},
        {"rooms": []},
        {"rooms": {"bedroom": {"needs_heat": "yes"}}},
        {"starving_since": {"bedroom": None}},
    ],
)
def test_bad_data_raises(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"expected|unsupported"):
        EngineState.from_dict({"version": 1, **changes})
