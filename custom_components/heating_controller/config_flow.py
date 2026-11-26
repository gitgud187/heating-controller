"""
Config flow: the plant in the main entry, one subentry per room, tuning in the options.

A room is its thermostat, wall sensor and floor area, plus optional valve sources.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.helpers import selector

from .adapter.options import OPTIONS
from .const import (
    CONF_AREA_M2,
    CONF_BOILER_SWITCH,
    CONF_HEARTBEAT,
    CONF_NOTIFY_SERVICE,
    CONF_OUTDOOR_SENSOR,
    CONF_POSITION_SENSOR,
    CONF_POWER_LEVEL,
    CONF_THERMOSTAT,
    CONF_VALVE,
    CONF_WALL_SENSOR,
    DOMAIN,
    SUBENTRY_ROOM,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


def _entity(domain: str, device_class: str | None = None) -> selector.EntitySelector:
    if device_class is None:
        return selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=domain, device_class=device_class)
    )


STEP_USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_BOILER_SWITCH): _entity("switch"),
        probatio.Required(CONF_POWER_LEVEL): _entity("number"),
        probatio.Required(CONF_HEARTBEAT): _entity("button"),
        probatio.Required(CONF_OUTDOOR_SENSOR): _entity("sensor", "temperature"),
        probatio.Optional(CONF_NOTIFY_SERVICE): selector.TextSelector(),
    },
)

ROOM_NAME = "name"


def _required(key: str, default: Any) -> probatio.Required:
    """Return a required field, pre-filled only when there is a value to offer."""
    if default is None:
        return probatio.Required(key)
    return probatio.Required(key, default=default)


def _optional(key: str, default: Any) -> probatio.Optional:
    """Return an optional field, pre-filled only when there is a value to offer."""
    if default is None:
        return probatio.Optional(key)
    return probatio.Optional(key, description={"suggested_value": default})


def _room_schema(defaults: Mapping[str, Any], *, with_name: bool) -> probatio.Schema:
    """
    One room: the thermostat that drives its valve, the wall sensor, the floor area.

    The valve sources are optional: a valve climate for its heating flag, a position sensor
    for its opening. Without either the thermostat's own `hvac_action` is used.
    """
    fields: dict[Any, Any] = {}
    if with_name:
        fields[_required(ROOM_NAME, defaults.get(ROOM_NAME))] = selector.TextSelector()
    fields[_required(CONF_THERMOSTAT, defaults.get(CONF_THERMOSTAT))] = _entity("climate")
    fields[_required(CONF_WALL_SENSOR, defaults.get(CONF_WALL_SENSOR))] = _entity(
        "sensor", "temperature"
    )
    fields[_required(CONF_AREA_M2, defaults.get(CONF_AREA_M2, 10.0))] = selector.NumberSelector(
        selector.NumberSelectorConfig(min=0.5, max=200, step=0.1, unit_of_measurement="m²")
    )
    fields[_optional(CONF_VALVE, defaults.get(CONF_VALVE))] = _entity("climate")
    fields[_optional(CONF_POSITION_SENSOR, defaults.get(CONF_POSITION_SENSOR))] = _entity("sensor")
    return probatio.Schema(fields)


class HeatingControllerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the single controller entry."""

    VERSION = 2

    @override
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the boiler, heartbeat, outdoor sensor and notification target."""
        if user_input is not None:
            return self.async_create_entry(title="Heating Controller", data=user_input)

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)

    @classmethod
    @override
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Rooms are subentries."""
        return {SUBENTRY_ROOM: RoomSubentryFlow}

    @staticmethod
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> HeatingControllerOptionsFlow:
        """Tuning options."""
        return HeatingControllerOptionsFlow()


class RoomSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure one room."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Name, thermostat, wall sensor, area and the optional valve sources."""
        if user_input is not None:
            data = dict(user_input)
            name = str(data.pop(ROOM_NAME)).strip()
            return self.async_create_entry(title=name, data=data)
        return self.async_show_form(step_id="user", data_schema=_room_schema({}, with_name=True))

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change a room's entities; the name is the subentry title and stays."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            return self.async_update_and_abort(self._get_entry(), subentry, data=dict(user_input))
        return self.async_show_form(
            step_id="reconfigure", data_schema=_room_schema(subentry.data, with_name=False)
        )


class HeatingControllerOptionsFlow(OptionsFlow):
    """Thresholds and gains; defaults are the engine's. The update listener reloads."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """One form with every option."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options
        schema = probatio.Schema(
            {
                probatio.Required(
                    option.key, default=current.get(option.key, option.default)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=option.minimum,
                        max=option.maximum,
                        step=option.step,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                )
                for option in OPTIONS
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
