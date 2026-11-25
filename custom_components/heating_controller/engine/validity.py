"""Whether a reading can be trusted."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

DEFAULT_STALE_AFTER = timedelta(minutes=20)


class Observation[T](Protocol):
    """Anything the snapshot saw: a value (None when unavailable) and when it was reported."""

    @property
    def value(self) -> T | None:
        """The observed value, None when unavailable."""
        ...

    @property
    def last_reported(self) -> datetime:
        """When the source last reported, changed or not."""
        ...


@dataclass(frozen=True, slots=True)
class Reading:
    """A numeric input as the snapshot saw it."""

    value: float | None
    last_reported: datetime


@dataclass(frozen=True, slots=True)
class Flag:
    """A yes/no input as the snapshot saw it, e.g. `hvac_action == heating`."""

    value: bool | None
    last_reported: datetime


def is_valid(
    reading: Observation[object], now: datetime, stale_after: timedelta = DEFAULT_STALE_AFTER
) -> bool:
    """Return whether the reading has a value and was reported recently enough."""
    if reading.value is None:
        return False
    return now - reading.last_reported < stale_after


def fresh_value[T](
    reading: Observation[T], now: datetime, stale_after: timedelta = DEFAULT_STALE_AFTER
) -> T | None:
    """Return the value if the reading is valid, else None."""
    return reading.value if is_valid(reading, now, stale_after) else None
