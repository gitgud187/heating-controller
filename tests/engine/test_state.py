from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from custom_components.heating_controller.engine.demand import RoomState
from custom_components.heating_controller.engine.guard import LoadSample
from custom_components.heating_controller.engine.state import STATE_VERSION, EngineState

from .factories import NOW

FULL = EngineState(
    on_since=NOW - timedelta(minutes=20),
    last_off=NOW - timedelta(hours=1),
    starts=(NOW - timedelta(minutes=50), NOW - timedelta(minutes=20)),
    lockout_until=NOW + timedelta(minutes=10),
    no_open_valve_since=NOW - timedelta(minutes=1),
    run_loads=(
        LoadSample(NOW - timedelta(minutes=20), 12.5),
        LoadSample(NOW - timedelta(minutes=19, seconds=0.25), 12.25),
        LoadSample(NOW, 11.0),
    ),
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
    assert EngineState.from_dict({"version": 2, "integral_pct": 3}).integral_pct == 3.0


def test_missing_keys_take_defaults() -> None:
    assert EngineState.from_dict({"version": 2}) == EngineState()


def test_room_without_fields_takes_defaults() -> None:
    state = EngineState.from_dict({"version": 2, "rooms": {"bedroom": {}}})
    assert state.rooms == {"bedroom": RoomState()}


def test_run_loads_are_stored_as_seconds_into_the_run() -> None:
    assert FULL.to_dict()["run_loads"] == [[0.0, 12.5], [59.75, 12.25], [1200.0, 11.0]]


def test_run_loads_without_a_run_are_dropped() -> None:
    state = EngineState.from_dict({"version": 2, "run_loads": [[0.0, 5.0]]})
    assert state.run_loads == ()
    assert EngineState(run_loads=FULL.run_loads).to_dict()["run_loads"] == []


def test_version_1_with_run_now_is_migrated() -> None:
    old = {
        key: value for key, value in FULL.to_dict().items() if key not in {"version", "run_loads"}
    } | {"version": 1, "run_now_until": (NOW + timedelta(minutes=5)).isoformat()}
    state = EngineState.from_dict(old)
    assert state == replace(FULL, run_loads=())
    saved = state.to_dict()
    assert saved["version"] == STATE_VERSION == 2
    assert "run_now_until" not in saved


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 3},
        {"version": 0},
        {"version": None},
        {"version": True},
        {"version": "2"},
        {"on_since": 5},
        {"starts": "2026-11-25T12:00:00+00:00"},
        {"integral_pct": "1.0"},
        {"integral_pct": True},
        {"modulation_pct": 5.5},
        {"modulation_pct": False},
        {"rooms": []},
        {"rooms": {"bedroom": {"needs_heat": "yes"}}},
        {"starving_since": {"bedroom": None}},
        {"run_loads": {}},
        {"run_loads": [5.0]},
        {"run_loads": [[0.0]]},
        {"run_loads": [[0.0, "5"]]},
    ],
)
def test_bad_data_raises(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"expected|unsupported"):
        EngineState.from_dict({"version": 2, **changes})
