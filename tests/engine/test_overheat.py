from __future__ import annotations

from datetime import timedelta

from custom_components.heating_controller.engine.config import EngineConfig, OverheatConfig
from custom_components.heating_controller.engine.demand import (
    RoomAssessment,
    RoomState,
    assess_room,
)
from custom_components.heating_controller.engine.model import ThermostatMode
from custom_components.heating_controller.engine.overheat import track_overheat

from .factories import NOW, room

CONFIG = OverheatConfig()


def assessed(wall: float, mode: ThermostatMode = ThermostatMode.HEAT) -> RoomAssessment:
    snap = room("bedroom", wall=wall, mode=mode)
    return assess_room(snap, RoomState(), NOW, EngineConfig())


def test_overheating_after_half_an_hour() -> None:
    clocks, hot = track_overheat({}, [assessed(23.0)], NOW, CONFIG)
    assert clocks == {"bedroom": NOW}
    assert hot == frozenset()
    later = NOW + timedelta(minutes=30)
    clocks, hot = track_overheat(clocks, [assessed(23.0)], later, CONFIG)
    assert hot == {"bedroom"}


def test_cooling_down_or_switching_off_resets() -> None:
    clocks = {"bedroom": NOW - timedelta(hours=1)}
    assert track_overheat(clocks, [assessed(22.4)], NOW, CONFIG) == ({}, frozenset())
    off = assessed(23.0, ThermostatMode.OFF)
    assert track_overheat(clocks, [off], NOW, CONFIG) == ({}, frozenset())
