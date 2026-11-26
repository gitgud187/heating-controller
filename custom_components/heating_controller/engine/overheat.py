"""Overheating: a room well above its target for a while, the sign of a valve held open."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .demand import RoomStatus

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import datetime

    from .config import OverheatConfig
    from .demand import RoomAssessment

# Rooms whose thermostat is heating; an off thermostat or an open window has a low target
# on purpose and is never "overheating".
HEATING = frozenset(
    {RoomStatus.SATISFIED, RoomStatus.SERVED, RoomStatus.WAITING_FOR_VALVE, RoomStatus.BLOCKED}
)


def track_overheat(
    since: Mapping[str, datetime],
    rooms: Iterable[RoomAssessment],
    now: datetime,
    config: OverheatConfig,
) -> tuple[dict[str, datetime], frozenset[str]]:
    """
    Update when each room went over its target by more than `above_k`.

    Only a room whose valve is open counts: that is a valve held open. A room that is merely
    cooling down after a setback has a closed valve. Returns the new clocks and the rooms that
    have been over for at least `after`. A room that is not heating (excluded, thermostat off,
    window open) loses its clock.
    """
    clocks: dict[str, datetime] = {}
    for room in rooms:
        over = room.error_k is not None and -room.error_k > config.above_k
        if room.status in HEATING and room.valve.open and over:
            clocks[room.name] = since.get(room.name, now)
    overheating = frozenset(name for name, start in clocks.items() if now - start >= config.after)
    return clocks, overheating
