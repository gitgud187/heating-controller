from __future__ import annotations

import pytest

from custom_components.heating_controller.engine.model import ValveProfile

from .factories import moes, room, snapshot, sonoff


def test_area_must_be_positive() -> None:
    with pytest.raises(ValueError, match="area must be positive"):
        room(area_m2=0.0)


def test_room_names_must_be_unique() -> None:
    with pytest.raises(ValueError, match="duplicate room names"):
        snapshot(room("bedroom"), room("bedroom"))


def test_valves_know_their_profile() -> None:
    assert moes().profile is ValveProfile.MOES
    assert sonoff().profile is ValveProfile.SONOFF


def test_valid_snapshot_keeps_its_rooms() -> None:
    assert [r.name for r in snapshot(room("bedroom"), room("kitchen")).rooms] == [
        "bedroom",
        "kitchen",
    ]
