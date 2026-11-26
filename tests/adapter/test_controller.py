from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import (
    async_fire_time_changed,
    async_mock_service,
    mock_restore_cache,
)

from custom_components.heating_controller.const import DOMAIN
from custom_components.heating_controller.engine.reasons import ReasonCode
from custom_components.heating_controller.engine.state import EngineState

from .conftest import Calls, make_entry, set_plant, setup

if TYPE_CHECKING:
    import pytest
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant.common import MockConfigEntry

    from custom_components.heating_controller.adapter.controller import HeatingController
    from custom_components.heating_controller.engine.evaluate import Evaluation


def controller(entry: MockConfigEntry) -> HeatingController:
    runtime: HeatingController = entry.runtime_data
    return runtime


def last(entry: MockConfigEntry) -> Evaluation:
    evaluation = controller(entry).last
    assert evaluation is not None
    return evaluation


async def test_cold_room_starts_the_boiler_output_first(hass: HomeAssistant, calls: Calls) -> None:
    set_plant(hass, bedroom_c=18.0)
    entry = await setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert last(entry).reason.code is ReasonCode.START_LOAD
    assert calls.set_value[0].data["value"] > 30
    assert [call.data["entity_id"] for call in calls.turn_on] == ["switch.boiler"]
    assert calls.press[0].data["entity_id"] == "button.boiler_heartbeat"
    state = hass.states.get("sensor.heating_controller_boiler")
    assert state is not None
    assert state.state == "on"
    assert state.attributes["reason"] == "start_load"


async def test_warm_house_switches_off_a_running_boiler(hass: HomeAssistant, calls: Calls) -> None:
    set_plant(hass, bedroom_c=21.5, bedroom_heating=False, boiler="on", power="60")
    await setup(hass)
    assert [call.data["entity_id"] for call in calls.turn_off] == ["switch.boiler"]
    assert calls.set_value[-1].data["value"] == 30
    assert calls.turn_on == []


async def test_nothing_written_while_the_boiler_is_unavailable(
    hass: HomeAssistant, calls: Calls
) -> None:
    set_plant(hass, boiler="unavailable")
    entry = await setup(hass)
    assert calls.turn_on == calls.turn_off == calls.set_value == []
    assert hass.states.get("sensor.heating_controller_boiler").state == "unavailable"  # type: ignore[union-attr]
    assert "boiler_unavailable" in controller(entry).problems.active


async def test_state_is_restored_from_storage(
    hass: HomeAssistant, calls: Calls, hass_storage: dict[str, object]
) -> None:
    on_since = dt_util.utcnow() - timedelta(minutes=5)
    stored = EngineState(on_since=on_since, starts=(on_since,), modulation_pct=40)
    entry = make_entry()
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": stored.to_dict(),
    }
    set_plant(hass, boiler="on", power="40")
    await setup(hass, entry)
    evaluation = last(entry)
    assert evaluation.reason.code in {ReasonCode.RUNNING_LOAD, ReasonCode.RUNNING_MIN_ON}
    assert evaluation.starts_last_hour == 1  # the restored start, no new one
    assert calls.turn_on == []


async def test_unreadable_storage_starts_fresh(
    hass: HomeAssistant, calls: Calls, hass_storage: dict[str, object]
) -> None:
    entry = make_entry()
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {"version": 99},
    }
    set_plant(hass)
    await setup(hass, entry)
    assert controller(entry).last is not None


async def test_timers_evaluate_and_press_the_heartbeat(hass: HomeAssistant, calls: Calls) -> None:
    set_plant(hass)
    entry = await setup(hass)
    evaluations, presses = len(controller(entry).history), len(calls.press)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert len(controller(entry).history) == evaluations + 1
    assert len(calls.press) == presses + 1


async def test_input_change_reevaluates_but_throttled(hass: HomeAssistant, calls: Calls) -> None:
    set_plant(hass)
    entry = await setup(hass)
    count = len(controller(entry).history)
    hass.states.async_set("sensor.bedroom_temperature", "18.4")
    hass.states.async_set("sensor.bedroom_temperature", "18.5")
    await hass.async_block_till_done()
    assert len(controller(entry).history) == count  # within 10 s of the last evaluation
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()
    assert len(controller(entry).history) == count + 1


def entity(hass: HomeAssistant, domain: str, entity_id: str) -> Any:
    """The entity object; the domain's services are mocked in these tests."""
    return hass.data["entity_components"][domain].get_entity(entity_id)


async def test_pause_switch_stops_the_boiler(hass: HomeAssistant, calls: Calls) -> None:
    set_plant(hass, boiler="on", power="60")
    entry = await setup(hass)
    pause = entity(hass, "switch", "switch.heating_controller_pause")
    await pause.async_turn_on()
    await hass.async_block_till_done()
    assert last(entry).reason.code is ReasonCode.PAUSED
    assert calls.turn_off[-1].data["entity_id"] == "switch.boiler"
    assert hass.states.get("sensor.heating_controller_boiler").state == "paused"  # type: ignore[union-attr]
    assert hass.states.get("switch.heating_controller_pause").state == "on"  # type: ignore[union-attr]
    await pause.async_turn_off()
    assert not controller(entry).controls.paused


async def test_pause_is_restored_before_the_first_evaluation(
    hass: HomeAssistant, calls: Calls
) -> None:
    mock_restore_cache(hass, [State("switch.heating_controller_pause", "on")])
    set_plant(hass, bedroom_c=18.0)
    entry = await setup(hass)
    assert last(entry).reason.code is ReasonCode.PAUSED
    assert calls.turn_on == []


async def test_clear_lockout_button(hass: HomeAssistant, calls: Calls) -> None:
    set_plant(hass)
    entry = await setup(hass)
    ctrl = controller(entry)
    ctrl.state = EngineState(lockout_until=dt_util.utcnow() + timedelta(hours=1))
    await ctrl.async_evaluate()
    assert hass.states.get("sensor.heating_controller_boiler").state == "locked_out"  # type: ignore[union-attr]
    await entity(hass, "button", "button.heating_controller_clear_lockout").async_press()
    assert ctrl.state.lockout_until is None


async def test_problem_creates_one_issue_and_one_notification(
    hass: HomeAssistant, calls: Calls
) -> None:
    set_plant(hass)
    hass.states.async_set("sensor.bedroom_temperature", "unavailable")
    entry = await setup(hass)
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, "wall_invalid_bedroom") is not None
    assert len(calls.notify) == 1
    assert "Bedroom" in calls.notify[0].data["message"]
    await controller(entry).async_evaluate()
    assert len(calls.notify) == 1  # same episode
    hass.states.async_set("sensor.bedroom_temperature", "18.0")
    await controller(entry).async_evaluate()
    assert registry.async_get_issue(DOMAIN, "wall_invalid_bedroom") is None
    hass.states.async_set("sensor.bedroom_temperature", "unavailable")
    await controller(entry).async_evaluate()
    assert len(calls.notify) == 2  # a new episode


async def test_no_notify_service_only_issues(hass: HomeAssistant, calls: Calls) -> None:
    data = {k: v for k, v in make_entry().data.items() if k != "notify_service"}
    set_plant(hass)
    hass.states.async_set("sensor.outdoor", "unavailable")
    entry = await setup(hass, make_entry(data=data))
    assert "outdoor_unavailable" in controller(entry).problems.active
    assert calls.notify == []


async def test_failing_service_is_logged_not_raised(hass: HomeAssistant, calls: Calls) -> None:
    async_mock_service(hass, "switch", "turn_on", raise_exception=HomeAssistantError("down"))
    set_plant(hass)
    entry = await setup(hass)
    assert entry.state is ConfigEntryState.LOADED


async def test_unload_stops_and_clears_issues(hass: HomeAssistant, calls: Calls) -> None:
    set_plant(hass)
    hass.states.async_set("sensor.bedroom_temperature", "unavailable")
    entry = await setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "wall_invalid_bedroom") is None
    presses = len(calls.press)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5))
    await hass.async_block_till_done()
    assert len(calls.press) == presses


async def test_problems_are_reported_only_once_they_last(
    hass: HomeAssistant,
    calls: Calls,
    monkeypatch: pytest.MonkeyPatch,
    freezer: FrozenDateTimeFactory,
) -> None:
    monkeypatch.setattr(
        "custom_components.heating_controller.adapter.controller.PROBLEM_DELAY",
        timedelta(minutes=3),
    )
    set_plant(hass, boiler="unavailable")
    hass.states.async_set("sensor.bedroom_temperature", "unavailable")
    entry = await setup(hass)
    ctrl = controller(entry)
    registry = ir.async_get(hass)
    assert "boiler_unavailable" in ctrl.problems.current
    assert ctrl.problems.active == {}
    assert calls.notify == []

    set_plant(hass)  # the board reconnected within the delay; the wall sensor is still gone
    hass.states.async_set("sensor.bedroom_temperature", "unavailable")
    freezer.tick(timedelta(minutes=4))
    await ctrl.async_evaluate()
    assert set(ctrl.problems.active) == {"wall_invalid_bedroom"}
    assert registry.async_get_issue(DOMAIN, "boiler_unavailable") is None
    assert len(calls.notify) == 1


async def test_evaluation_waits_for_home_assistant_to_start(
    hass: HomeAssistant, calls: Calls
) -> None:
    set_plant(hass, bedroom_c=18.0)
    hass.set_state(CoreState.starting)
    entry = await setup(hass)
    assert controller(entry).last is None
    assert calls.turn_on == []
    assert calls.press  # the heartbeat runs from the start
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert last(entry).reason.code is ReasonCode.START_LOAD
    assert calls.turn_on


async def test_notify_entity_gets_send_message(hass: HomeAssistant, calls: Calls) -> None:
    send = async_mock_service(hass, "notify", "send_message")
    hass.states.async_set("notify.chat", "unknown")
    data = dict(make_entry().data) | {"notify_service": "notify.chat"}
    set_plant(hass)
    hass.states.async_set("sensor.outdoor", "unavailable")
    await setup(hass, make_entry(data=data))
    assert [call.data["entity_id"] for call in send] == ["notify.chat"]
    assert "Outdoor" in send[0].data["message"]


async def test_missing_room_entity_is_reported_and_only_that_room_left_out(
    hass: HomeAssistant, calls: Calls
) -> None:
    set_plant(hass, bedroom_c=18.0)
    hass.states.async_remove("sensor.bedroom_trv_position")
    entry = await setup(hass)
    assert last(entry).reason.code is ReasonCode.START_LOAD  # its heating flag still counts
    assert "entity_missing" in controller(entry).problems.active
    assert "sensor.bedroom_trv_position" in calls.notify[0].data["message"]


async def test_heartbeat_is_withheld_when_evaluations_stop(
    hass: HomeAssistant, calls: Calls, freezer: FrozenDateTimeFactory
) -> None:
    set_plant(hass)
    entry = await setup(hass)
    ctrl = controller(entry)
    now = dt_util.utcnow()
    assert ctrl.heartbeat_due(now)
    assert not ctrl.heartbeat_due(now + timedelta(minutes=3))
    presses = len(calls.press)
    freezer.tick(timedelta(minutes=3))
    await ctrl._async_press_heartbeat()
    assert len(calls.press) == presses


async def test_heartbeat_before_the_first_evaluation_is_limited(
    hass: HomeAssistant, calls: Calls
) -> None:
    set_plant(hass)
    hass.set_state(CoreState.starting)
    entry = await setup(hass)
    ctrl = controller(entry)
    now = dt_util.utcnow()
    assert ctrl.heartbeat_due(now)
    assert not ctrl.heartbeat_due(now + timedelta(minutes=11))


async def test_concurrent_evaluations_run_one_after_the_other(
    hass: HomeAssistant, calls: Calls
) -> None:
    set_plant(hass, bedroom_c=18.0)
    entry = await setup(hass)
    ctrl = controller(entry)
    ctrl.state = EngineState()  # forget the start so both would want to start
    first, second = await asyncio.gather(ctrl.async_evaluate(), ctrl.async_evaluate())
    assert first.reason.code is ReasonCode.START_LOAD
    assert second.reason.code is not ReasonCode.START_LOAD  # it saw the first start
    assert len(ctrl.state.starts) == 1
