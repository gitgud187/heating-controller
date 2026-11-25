"""Whether a reading can be trusted."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

DEFAULT_STALE_AFTER = timedelta(minutes=20)


@dataclass(frozen=True, slots=True)
class Reading:
    """A numeric input as the snapshot saw it."""

    value: float | None
    last_reported: datetime


def is_valid(reading: Reading, now: datetime, stale_after: timedelta = DEFAULT_STALE_AFTER) -> bool:
    """Return whether the reading has a value and was reported recently enough."""
    if reading.value is None:
        return False
    return now - reading.last_reported < stale_after
