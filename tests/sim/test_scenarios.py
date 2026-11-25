from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.heating_controller.engine.reasons import ReasonCode
from sim.loop import simulate
from sim.metrics import assess, find_runs
from sim.scenarios import SCENARIOS, integration_hung, long_recovery


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario_passes(name: str) -> None:
    report = assess(simulate(SCENARIOS[name]()))
    assert report.failures == ()
    assert report.passed


def test_every_evaluation_restores_the_state_from_its_dict() -> None:
    result = simulate(integration_hung())
    evaluated = sum(1 for sample in result.samples if sample.evaluated)
    assert result.restored_states == evaluated == len(result.samples) - 10


def test_hung_integration_restart_waits_out_the_minimum_off() -> None:
    result = simulate(integration_hung())
    runs = find_runs(result.samples)
    stop = next(run for run in runs if run.stop == "failsafe_heartbeat")
    later = next(run for run in runs if run.start > stop.end)
    assert later.start - stop.end >= timedelta(minutes=8)
    assert any(ReasonCode.EXTERNAL_STOP in sample.reasons for sample in result.samples)


def test_long_recovery_outlasts_the_progress_check_in_the_default_house() -> None:
    result = simulate(long_recovery())
    first = find_runs(result.samples)[0]
    assert first.duration > result.scenario.config.guard.progress_check_after
    assert first.stop == ReasonCode.STOP_LOAD
