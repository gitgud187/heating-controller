from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry

from custom_components.heating_controller.const import (
    CONF_BOILER_SWITCH,
    CONF_HEARTBEAT,
    CONF_OUTDOOR_SENSOR,
    CONF_POWER_LEVEL,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

USER_INPUT = {
    CONF_BOILER_SWITCH: "switch.boiler",
    CONF_POWER_LEVEL: "number.boiler_power",
    CONF_HEARTBEAT: "button.boiler_heartbeat",
    CONF_OUTDOOR_SENSOR: "sensor.outdoor",
}


async def test_user_step_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == USER_INPUT


async def test_only_one_entry(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, data=USER_INPUT).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"
