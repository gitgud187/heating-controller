from __future__ import annotations

import math
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from custom_components.heating_controller.engine import modulation
from custom_components.heating_controller.engine.config import EngineConfig, ModulationConfig
from custom_components.heating_controller.engine.demand import RoomState, assess_room
from sim import tune
from sim.house import default_house
from sim.scenarios import clear_lockout, cold_night
from tests.engine.factories import NOW, room

if TYPE_CHECKING:
    from sim.loop import Scenario

CONFIG = ModulationConfig()


def step(integral: float, error: float, proportional: float, running: timedelta | None) -> float:
    return tune.conditional_integral(
        integral_pct=integral,
        error_k=error,
        proportional_pct=proportional,
        dt=timedelta(minutes=6),
        running_for=running,
        config=CONFIG,
    )


def test_conditional_integration_freezes_only_into_saturation() -> None:
    on = timedelta(minutes=30)
    assert step(5.0, 1.0, 96.0, on) == 5.0  # at 100, pushing up
    assert step(5.0, -1.0, 96.0, on) < 5.0  # at 100, pulling back
    assert step(-5.0, 1.0, 34.0, on) > -5.0  # at 30, pushing back up
    assert step(-5.0, -1.0, 34.0, on) == -5.0  # at 30, pushing down
    assert step(29.9, 1.0, 50.0, on) == 30.0  # bounded
    assert step(5.0, 1.0, 50.0, timedelta(minutes=5)) == 5.0  # integral delay
    assert step(6.0, 1.0, 50.0, None) == pytest.approx(6.0 * math.exp(-1 / 60))


def test_signed_error_counts_satisfied_rooms() -> None:
    config = EngineConfig()
    cold = assess_room(room("cold", area_m2=10.0, wall=20.0), RoomState(), NOW, config)
    warm = assess_room(room("warm", area_m2=10.0, wall=22.0), RoomState(), NOW, config)
    assert tune.signed_error([cold, warm]) == pytest.approx(0.0)
    assert modulation.weighted_error([cold, warm]) == pytest.approx(1.0)
    assert tune.signed_error([]) == 0.0


def test_variant_and_plant() -> None:
    config = tune.Variant("x", kp=20.0, ki=3.0).config(EngineConfig())
    assert (config.modulation.kp_pct_per_k, config.modulation.ki_pct_per_k_h) == (20.0, 3.0)
    house = tune.Plant("p", radiator=2.0, capacity=0.5).apply(default_house())
    original = default_house().rooms[0].params
    assert house.rooms[0].params.radiator_w == 2 * original.radiator_w
    assert house.rooms[0].params.capacity_j_per_k == 0.5 * original.capacity_j_per_k


def short() -> Scenario:
    return replace(cold_night(), duration=timedelta(hours=4))


def test_swapped_functions_are_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tune, "SCENARIOS", {"short": short, "clear_lockout": clear_lockout})
    original = modulation.update_integral
    variant = tune.Variant("signed", integral=tune.conditional_integral, error=tune.signed_error)
    reports = tune.run_variant(variant, tune.PLANTS[:1], (1,))
    assert modulation.update_integral is original
    assert [name for name, _ in reports] == ["short", "clear_lockout"]
    row = tune.summarise(variant, reports)
    assert row.startswith("| signed |")


def test_main_quick(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        tune, "SCENARIOS", {"cold_night": short, "mild_day": short, "clear_lockout": short}
    )
    monkeypatch.setattr(tune, "VARIANTS", tune.VARIANTS[:1])
    assert tune.main(["--quick"]) == 0
    out = capsys.readouterr().out
    assert "| variant |" in out
    assert "spec" in out
