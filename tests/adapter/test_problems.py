from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

from custom_components.heating_controller.adapter.problems import ProblemKind, problems_of
from custom_components.heating_controller.engine.evaluate import Command
from custom_components.heating_controller.engine.reasons import Reason, ReasonCode

from .conftest import Calls, set_plant, setup

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.heating_controller.engine.evaluate import Evaluation


def with_reason(
    evaluation: Evaluation, code: ReasonCode, message: str, **state: object
) -> Evaluation:
    return replace(
        evaluation,
        reasons=(Reason(code, message),),
        command=Command(heat=False, modulation_pct=30),
        state=replace(evaluation.state, **state),  # type: ignore[arg-type]
    )


async def test_lockout_lasts_while_locked_and_no_open_valve_until_a_start(
    hass: HomeAssistant, calls: Calls
) -> None:
    set_plant(hass)
    entry = await setup(hass)
    base = entry.runtime_data.last
    until = dt_util.utcnow() + timedelta(hours=1)
    stalled = with_reason(
        base, ReasonCode.NO_PROGRESS, "stopped: no room closer", lockout_until=until
    )
    found = problems_of(stalled, frozenset(), {})
    assert found[ProblemKind.LOCKOUT.value].message == "Boiler stopped: no room closer"
    waiting = with_reason(base, ReasonCode.LOCKOUT, "waiting: locked out", lockout_until=until)
    assert ProblemKind.LOCKOUT.value in problems_of(waiting, frozenset(), found)
    over = with_reason(base, ReasonCode.IDLE, "idle", lockout_until=None)
    assert ProblemKind.LOCKOUT.value not in problems_of(over, frozenset(), found)

    stop = with_reason(base, ReasonCode.NO_OPEN_VALVE, "stopped: no open valve for 3 min")
    found = problems_of(stop, frozenset(), {})
    assert ProblemKind.NO_OPEN_VALVE.value in found
    veto = with_reason(base, ReasonCode.NO_OPEN_VALVE, "waiting: no valve is open")
    assert ProblemKind.NO_OPEN_VALVE.value in problems_of(veto, frozenset(), found)
    assert ProblemKind.NO_OPEN_VALVE.value not in problems_of(veto, frozenset(), {})
    started = replace(base, command=Command(heat=True, modulation_pct=50))
    assert ProblemKind.NO_OPEN_VALVE.value not in problems_of(started, frozenset(), found)


async def test_overheating_room_is_a_problem(hass: HomeAssistant, calls: Calls) -> None:
    set_plant(hass)
    entry = await setup(hass)
    found = problems_of(entry.runtime_data.last, frozenset({"Living"}), {})
    assert "room_overheating_living" in found


async def test_unhealthy_and_missing_valves(hass: HomeAssistant, calls: Calls) -> None:
    set_plant(hass)
    hass.states.async_set("select.living_trv_sensor", "external")
    hass.states.async_set("climate.bedroom_trv", "unavailable")
    hass.states.async_set("sensor.bedroom_trv_position", "unavailable")
    entry = await setup(hass)
    active = entry.runtime_data.problems.active
    assert "valve_unhealthy_living" in active
    assert "valve_data_missing_bedroom" in active
    state = hass.states.get("binary_sensor.living_valve_problem")
    assert state is not None
    assert state.state == "on"


async def test_notify_service_without_domain(hass: HomeAssistant, calls: Calls) -> None:
    from .conftest import make_entry  # noqa: PLC0415 - local to this case

    data = dict(make_entry().data) | {"notify_service": "phone"}
    set_plant(hass)
    hass.states.async_set("sensor.outdoor", "unavailable")
    await setup(hass, make_entry(data=data))
    assert len(calls.notify) == 1
