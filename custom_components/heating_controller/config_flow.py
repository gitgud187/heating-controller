"""Config flow: the plant-wide entities. Rooms are added as subentries later."""

from __future__ import annotations

from typing import Any, override

import probatio
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_BOILER_SWITCH,
    CONF_HEARTBEAT,
    CONF_NOTIFY_SERVICE,
    CONF_OUTDOOR_SENSOR,
    CONF_POWER_LEVEL,
    DOMAIN,
)

STEP_USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_BOILER_SWITCH): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="switch"),
        ),
        probatio.Required(CONF_POWER_LEVEL): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="number"),
        ),
        probatio.Required(CONF_HEARTBEAT): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="button"),
        ),
        probatio.Required(CONF_OUTDOOR_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="temperature"),
        ),
        probatio.Optional(CONF_NOTIFY_SERVICE): selector.TextSelector(),
    },
)


class HeatingControllerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the single controller entry."""

    VERSION = 1

    @override
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the boiler, heartbeat, outdoor sensor and notification target."""
        if user_input is not None:
            return self.async_create_entry(title="Heating Controller", data=user_input)

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)
