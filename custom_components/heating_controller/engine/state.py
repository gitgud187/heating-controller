"""Engine state that must survive a restart, with a JSON-safe round trip."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from .demand import RoomState

if TYPE_CHECKING:
    from collections.abc import Mapping

STATE_VERSION = 1


@dataclass(frozen=True, slots=True)
class EngineState:
    """
    Everything the engine remembers between evaluations.

    `on_since` is None while the boiler is off. `modulation_pct` is the last value written.
    """

    on_since: datetime | None = None
    last_off: datetime | None = None
    starts: tuple[datetime, ...] = ()
    lockout_until: datetime | None = None
    no_open_valve_since: datetime | None = None
    run_now_until: datetime | None = None
    integral_pct: float = 0.0
    modulation_pct: int | None = None
    last_evaluated: datetime | None = None
    rooms: Mapping[str, RoomState] = field(default_factory=dict)
    starving_since: Mapping[str, datetime] = field(default_factory=dict)

    @property
    def boiler_on(self) -> bool:
        """Whether the engine last commanded the boiler on."""
        return self.on_since is not None

    def to_dict(self) -> dict[str, object]:
        """Serialise to plain JSON types; datetimes become ISO strings."""
        return {
            "version": STATE_VERSION,
            "on_since": _iso(self.on_since),
            "last_off": _iso(self.last_off),
            "starts": [start.isoformat() for start in self.starts],
            "lockout_until": _iso(self.lockout_until),
            "no_open_valve_since": _iso(self.no_open_valve_since),
            "run_now_until": _iso(self.run_now_until),
            "integral_pct": self.integral_pct,
            "modulation_pct": self.modulation_pct,
            "last_evaluated": _iso(self.last_evaluated),
            "rooms": {
                name: {"needs_heat": room.needs_heat, "blocked_since": _iso(room.blocked_since)}
                for name, room in self.rooms.items()
            },
            "starving_since": {
                name: since.isoformat() for name, since in self.starving_since.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> EngineState:
        """Restore from `to_dict` output. Missing keys take defaults; bad data raises ValueError."""
        if data.get("version") != STATE_VERSION:
            msg = f"unsupported state version: {data.get('version')!r}"
            raise ValueError(msg)
        return cls(
            on_since=_opt_datetime(data.get("on_since")),
            last_off=_opt_datetime(data.get("last_off")),
            starts=tuple(_datetime(item) for item in _list(data.get("starts", []))),
            lockout_until=_opt_datetime(data.get("lockout_until")),
            no_open_valve_since=_opt_datetime(data.get("no_open_valve_since")),
            run_now_until=_opt_datetime(data.get("run_now_until")),
            integral_pct=_float(data.get("integral_pct", 0.0)),
            modulation_pct=_opt_int(data.get("modulation_pct")),
            last_evaluated=_opt_datetime(data.get("last_evaluated")),
            rooms={name: _room(value) for name, value in _mapping(data.get("rooms", {})).items()},
            starving_since={
                name: _datetime(value)
                for name, value in _mapping(data.get("starving_since", {})).items()
            },
        )


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _invalid(value: object, expected: str) -> ValueError:
    return ValueError(f"expected {expected}, got {value!r}")


def _datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise _invalid(value, "an ISO datetime string")
    return datetime.fromisoformat(value)


def _opt_datetime(value: object) -> datetime | None:
    return None if value is None else _datetime(value)


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _invalid(value, "a number")
    return float(value)


def _opt_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid(value, "an integer")
    return value


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise _invalid(value, "a list")
    return value


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _invalid(value, "an object")
    return {str(key): item for key, item in value.items()}


def _room(value: object) -> RoomState:
    data = _mapping(value)
    needs_heat = data.get("needs_heat", False)
    if not isinstance(needs_heat, bool):
        raise _invalid(needs_heat, "a boolean")
    return RoomState(needs_heat=needs_heat, blocked_since=_opt_datetime(data.get("blocked_since")))
