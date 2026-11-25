from __future__ import annotations

from datetime import timedelta

from custom_components.heating_controller.adapter.options import OPTIONS, engine_config
from custom_components.heating_controller.engine.config import EngineConfig


def test_defaults_match_the_engine() -> None:
    assert engine_config({}) == EngineConfig()
    defaults = {option.key: option.default for option in OPTIONS}
    assert engine_config(defaults) == EngineConfig()
    assert defaults["max_run_h"] == 6.0
    assert defaults["min_off_min"] == 8.0


def test_options_override_the_engine() -> None:
    config = engine_config(
        {"max_run_h": 5, "min_off_min": 10.0, "max_starts": 4.0, "kp_pct_per_k": 12, "x": 1}
    )
    assert config.guard.max_run == timedelta(hours=5)
    assert config.guard.min_off == timedelta(minutes=10)
    assert config.guard.max_starts == 4
    assert config.modulation.kp_pct_per_k == 12.0


def test_non_numbers_are_ignored() -> None:
    assert engine_config({"max_run_h": "5", "max_starts": True}) == EngineConfig()
