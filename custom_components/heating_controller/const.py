"""Constants shared by the Home Assistant side of the integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "heating_controller"

# Main entry: the plant.
CONF_BOILER_SWITCH: Final = "boiler_switch"
CONF_POWER_LEVEL: Final = "power_level"
CONF_HEARTBEAT: Final = "heartbeat"
CONF_OUTDOOR_SENSOR: Final = "outdoor_sensor"
CONF_NOTIFY_SERVICE: Final = "notify_service"

# Room subentries.
SUBENTRY_ROOM: Final = "room"
CONF_THERMOSTAT: Final = "thermostat"
CONF_WALL_SENSOR: Final = "wall_sensor"
CONF_AREA_M2: Final = "area_m2"
CONF_VALVE_PROFILE: Final = "valve_profile"
CONF_VALVE: Final = "valve"
CONF_POSITION_SENSOR: Final = "position_sensor"
CONF_MODE_SELECT: Final = "mode_select"
CONF_OPENING_NUMBER: Final = "opening_number"
CONF_CLOSING_NUMBER: Final = "closing_number"
CONF_SMART_SWITCH: Final = "smart_switch"
CONF_SENSOR_SELECT: Final = "sensor_select"

MOES_ENTITIES: Final = (CONF_POSITION_SENSOR, CONF_MODE_SELECT)
SONOFF_ENTITIES: Final = (
    CONF_OPENING_NUMBER,
    CONF_CLOSING_NUMBER,
    CONF_SMART_SWITCH,
    CONF_SENSOR_SELECT,
)

# Controller timing.
EVALUATE_INTERVAL: Final = timedelta(seconds=60)
HEARTBEAT_INTERVAL: Final = timedelta(seconds=60)
# A change of an input re-evaluates at once, but at most this often.
MIN_REEVALUATE_GAP: Final = timedelta(seconds=10)
HISTORY_LENGTH: Final = 200
# After a start, inputs such as the boiler board or the notification service may still be
# reconnecting. Problems are tracked from the first evaluation but reported only after this.
STARTUP_GRACE: Final = timedelta(minutes=3)

STORAGE_VERSION: Final = 1
