from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.heating_controller.engine.config import ValveConfig
from custom_components.heating_controller.engine.model import Valve
from custom_components.heating_controller.engine.validity import Reading
from custom_components.heating_controller.engine.valves import ValveAssessment, assess_valve

from .factories import NOW, moes, sonoff

OLD = NOW - timedelta(hours=6)


def assess(valve: Valve) -> ValveAssessment:
    return assess_valve(valve, ValveConfig())


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
def test_heating_flag_and_position(
    heating: bool | None,  # noqa: FBT001 - parametrized
    position: float | None,
    is_open: bool,  # noqa: FBT001 - parametrized
    missing: bool,  # noqa: FBT001 - parametrized
) -> None:
    result = assess(moes(heating=heating, position=position))
    assert result.open is is_open
    assert result.data_missing is missing


def test_old_data_is_still_trusted() -> None:
    # Valves report only on change; hours-old data is normal, not missing.
    assert assess(moes(heating=False, position=40.0, at=OLD)) == ValveAssessment(
        open=True, data_missing=False
    )
    assert assess(sonoff(heating=True, at=OLD)).open


@pytest.mark.parametrize(
    ("heating", "is_open", "missing"),
    [(True, True, False), (False, False, False), (None, False, True)],
)
def test_heating_flag_only(
    heating: bool | None,  # noqa: FBT001 - parametrized
    is_open: bool,  # noqa: FBT001 - parametrized
    missing: bool,  # noqa: FBT001 - parametrized
) -> None:
    result = assess(sonoff(heating=heating))
    assert result.open is is_open
    assert result.data_missing is missing


def test_position_only_and_no_source() -> None:
    assert assess(Valve(position=Reading(30.0, NOW))).open
    assert assess(Valve()) == ValveAssessment(open=False, data_missing=True)
