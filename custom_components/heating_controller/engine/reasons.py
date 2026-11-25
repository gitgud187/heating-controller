"""Why the engine decided what it decided: a machine code and a sentence."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime, timedelta


class ReasonCode(StrEnum):
    """Every decision the core or the guard can make."""

    # core
    START_LOAD = "start_load"
    START_STARVATION = "start_starvation"
    RUN_NOW = "run_now"
    RUNNING_LOAD = "running_load"
    RUNNING_MIN_ON = "running_min_on"
    STOP_LOAD = "stop_load"
    WAITING_LOAD = "waiting_load"
    IDLE = "idle"
    ALL_ROOMS_EXCLUDED = "all_rooms_excluded"
    BOILER_UNAVAILABLE = "boiler_unavailable"
    EXTERNAL_STOP = "external_stop"
    # guard
    PAUSED = "paused"
    LOCKOUT = "lockout"
    MAX_RUN = "max_run"
    NO_OPEN_VALVE = "no_open_valve"
    MIN_OFF = "min_off"
    START_LIMIT = "start_limit"
    SNAPSHOT_INCOMPLETE = "snapshot_incomplete"
    SNAPSHOT_OLD = "snapshot_old"


@dataclass(frozen=True, slots=True)
class Reason:
    """A decision reason: `code` for machines, `message` for people."""

    code: ReasonCode
    message: str


def minutes(duration: timedelta) -> int:
    """Whole minutes, rounded up, for messages ("3 min left")."""
    return max(0, math.ceil(duration.total_seconds() / 60))


def clock(moment: datetime) -> str:
    """Time of day for messages ("until 14:05")."""
    return moment.strftime("%H:%M")
