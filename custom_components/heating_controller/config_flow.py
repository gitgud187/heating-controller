"""
Config flow: the plant in the main entry, one subentry per room, tuning in the options.

A room is added in two steps: the common entities, then the entities its valve profile needs.
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
    CONF_CLOSING_NUMBER,
    CONF_HEARTBEAT,
    CONF_MODE_SELECT,
    CONF_NOTIFY_SERVICE,
    CONF_OPENING_NUMBER,
    CONF_OUTDOOR_SENSOR,
    CONF_POSITION_SENSOR,
    CONF_POWER_LEVEL,
    CONF_SENSOR_SELECT,
    CONF_SMART_SWITCH,
    CONF_THERMOSTAT,
    CONF_VALVE,
    CONF_VALVE_PROFILE,
    CONF_WALL_SENSOR,
    DOMAIN,
    SUBENTRY_ROOM,
)
from .engine.model import ValveProfile

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

ROOM_PROFILE_ENTITIES: dict[ValveProfile, dict[str, selector.EntitySelector]] = {
    ValveProfile.MOES: {
        CONF_POSITION_SENSOR: _entity("sensor"),
        CONF_MODE_SELECT: _entity("select"),
    },
    ValveProfile.SONOFF: {
        CONF_OPENING_NUMBER: _entity("number"),
        CONF_CLOSING_NUMBER: _entity("number"),
        CONF_SMART_SWITCH: _entity("switch"),
        CONF_SENSOR_SELECT: _entity("select"),
    },
}


def _required(key: str, default: Any) -> probatio.Required:
    """Return a required field, pre-filled only when there is a value to offer."""
    if default is None:
        return probatio.Required(key)
    return probatio.Required(key, default=default)


def _room_schema(defaults: Mapping[str, Any], *, with_name: bool) -> probatio.Schema:
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
    fields[
        _required(CONF_VALVE_PROFILE, defaults.get(CONF_VALVE_PROFILE, ValveProfile.MOES.value))
    ] = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[profile.value for profile in ValveProfile],
            translation_key=CONF_VALVE_PROFILE,
        )
    )
    fields[_required(CONF_VALVE, defaults.get(CONF_VALVE))] = _entity("climate")
    return probatio.Schema(fields)


def _profile_schema(profile: ValveProfile, defaults: Mapping[str, Any]) -> probatio.Schema:
    return probatio.Schema(
        {
            _required(key, defaults.get(key)): entity
            for key, entity in ROOM_PROFILE_ENTITIES[profile].items()
        }
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

    def __init__(self) -> None:
        """Start with no data."""
        self._data: dict[str, Any] = {}
        self._reconfiguring = False

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Name, thermostat, wall sensor, area, valve profile and valve."""
        if user_input is not None:
            self._data = dict(user_input)
            return await self.async_step_valve()
        return self.async_show_form(step_id="user", data_schema=_room_schema({}, with_name=True))

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change a room's entities; the name is the subentry title and stays."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            self._data = dict(subentry.data) | dict(user_input)
            self._reconfiguring = True
            return await self.async_step_valve()
        return self.async_show_form(
            step_id="reconfigure", data_schema=_room_schema(subentry.data, with_name=False)
        )

    async def async_step_valve(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for the entities the valve profile needs."""
        profile = ValveProfile(self._data[CONF_VALVE_PROFILE])
        if user_input is not None:
            keep = {key for keys in ROOM_PROFILE_ENTITIES.values() for key in keys}
            data = {k: v for k, v in self._data.items() if k not in keep} | dict(user_input)
            if self._reconfiguring:
                return self.async_update_and_abort(
                    self._get_entry(), self._get_reconfigure_subentry(), data=data
                )
            name = str(data.pop(ROOM_NAME)).strip()
            return self.async_create_entry(title=name, data=data)
        return self.async_show_form(
            step_id="valve",
            data_schema=_profile_schema(profile, self._data),
            description_placeholders={"profile": profile.value},
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
