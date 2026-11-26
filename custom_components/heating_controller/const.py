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
CONF_VALVE: Final = "valve"  # optional: a climate whose hvac_action says the valve heats
CONF_POSITION_SENSOR: Final = "position_sensor"  # optional: the valve opening in percent

# Keys of the version 1 room format, removed when an entry is migrated to version 2.
LEGACY_ROOM_KEYS: Final = (
    "valve_profile",
    "mode_select",
    "opening_number",
    "closing_number",
    "smart_switch",
    "sensor_select",
)

# Controller timing.
EVALUATE_INTERVAL: Final = timedelta(seconds=60)
HEARTBEAT_INTERVAL: Final = timedelta(seconds=60)
# The heartbeat is withheld when no evaluation has succeeded for this long, so a controller
# that keeps failing lets the board's watchdog switch the boiler off.
HEARTBEAT_STALE_AFTER: Final = timedelta(seconds=150)
# Before the first evaluation (Home Assistant still starting) the heartbeat runs this long.
BOOT_HEARTBEAT: Final = timedelta(minutes=10)
# A change of an input re-evaluates at once, but at most this often.
MIN_REEVALUATE_GAP: Final = timedelta(seconds=10)
HISTORY_LENGTH: Final = 200
# A problem is reported only once it has lasted this long: a restart, a reconnecting board or
# a valve that briefly reports odd settings must not raise an issue or a notification.
PROBLEM_DELAY: Final = timedelta(minutes=3)

STORAGE_VERSION: Final = 1
