from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from custom_components.heating_controller.engine.config import ValveConfig
from custom_components.heating_controller.engine.valves import ValveAssessment, assess_valve

from .factories import NOW, moes, sonoff

if TYPE_CHECKING:
    from custom_components.heating_controller.engine.model import Valve

STALE = timedelta(minutes=20)
OLD = NOW - timedelta(minutes=30)


def assess(valve: Valve) -> ValveAssessment:
    return assess_valve(valve, NOW, STALE, ValveConfig())


@pytest.mark.parametrize(
    ("heating", "position", "is_open", "missing"),
    [
        (True, 0.0, True, False),
        (False, 6.0, True, False),
        (False, 5.0, False, False),
        (None, 50.0, True, False),
        (True, None, True, False),
        (False, None, False, False),
        (None, None, False, True),
    ],
)
def test_moes_open_and_missing(
    heating: bool | None,  # noqa: FBT001 - parametrized
    position: float | None,
    is_open: bool,  # noqa: FBT001 - parametrized
    missing: bool,  # noqa: FBT001 - parametrized
) -> None:
    result = assess(moes(heating=heating, position=position))
    assert result.open is is_open
    assert result.data_missing is missing


def test_moes_stale_data_counts_as_missing() -> None:
    result = assess(moes(at=OLD))
    assert result == ValveAssessment(open=False, healthy=True, data_missing=True)


@pytest.mark.parametrize(
    ("preset", "mode", "healthy"),
    [
        ("manual", "comfort", True),
        ("on", "comfort", False),
        ("manual", "eco", False),
        (None, "comfort", False),
    ],
)
def test_moes_health(
    preset: str | None,
    mode: str | None,
    healthy: bool,  # noqa: FBT001 - parametrized
) -> None:
    assert assess(moes(preset=preset, mode=mode)).healthy is healthy


@pytest.mark.parametrize(
    ("heating", "is_open", "missing"),
    [(True, True, False), (False, False, False), (None, False, True)],
)
def test_sonoff_open_and_missing(
    heating: bool | None,  # noqa: FBT001 - parametrized
    is_open: bool,  # noqa: FBT001 - parametrized
    missing: bool,  # noqa: FBT001 - parametrized
) -> None:
    result = assess(sonoff(heating=heating))
    assert result.open is is_open
    assert result.data_missing is missing


def test_sonoff_stale_hvac_action_counts_as_missing() -> None:
    assert assess(sonoff(at=OLD)).data_missing


def test_sonoff_healthy_defaults() -> None:
    assert assess(sonoff()).healthy


@pytest.mark.parametrize(
    "valve",
    [
        sonoff(system_mode="auto"),
        sonoff(opening=0.0),
        sonoff(closing=None),
        sonoff(smart="on"),
        sonoff(sensor_select="external"),
    ],
)
def test_sonoff_unhealthy(valve: Valve) -> None:
    assert not assess(valve).healthy
