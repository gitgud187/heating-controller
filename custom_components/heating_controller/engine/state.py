"""Engine state that must survive a restart, with a JSON-safe round trip."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .demand import RoomState
from .guard import LoadSample

if TYPE_CHECKING:
    from collections.abc import Mapping

STATE_VERSION = 2
# Version 1 also stored `run_now_until` (the removed run-now button); it is ignored on load.
READABLE_VERSIONS = frozenset({1, STATE_VERSION})


@dataclass(frozen=True, slots=True)
class EngineState:
    """
    Everything the engine remembers between evaluations.

    `on_since` is None while the boiler is off. `modulation_pct` is the last value written.
    `run_loads` is the sampled load of the current run for the guard's progress check; it is
    empty while the boiler is off and stored as seconds since `on_since`.
    """

    on_since: datetime | None = None
    last_off: datetime | None = None
    starts: tuple[datetime, ...] = ()
    lockout_until: datetime | None = None
    no_open_valve_since: datetime | None = None
    run_loads: tuple[LoadSample, ...] = ()
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
            "run_loads": _run_loads_to_list(self.run_loads, self.on_since),
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
        """
        Restore from `to_dict` output of this or an older version.

        Missing keys take defaults, keys of older versions are ignored; bad data raises
        ValueError.
        """
        version = data.get("version")
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or (version not in READABLE_VERSIONS)
        ):
            msg = f"unsupported state version: {version!r}"
            raise ValueError(msg)
        on_since = _opt_datetime(data.get("on_since"))
        return cls(
            on_since=on_since,
            last_off=_opt_datetime(data.get("last_off")),
            starts=tuple(_datetime(item) for item in _list(data.get("starts", []))),
            lockout_until=_opt_datetime(data.get("lockout_until")),
            no_open_valve_since=_opt_datetime(data.get("no_open_valve_since")),
            run_loads=_run_loads(data.get("run_loads", []), on_since),
            integral_pct=_float(data.get("integral_pct", 0.0)),
            modulation_pct=_opt_int(data.get("modulation_pct")),
            last_evaluated=_opt_datetime(data.get("last_evaluated")),
            rooms={name: _room(value) for name, value in _mapping(data.get("rooms", {})).items()},
            starving_since={
                name: _datetime(value)
                for name, value in _mapping(data.get("starving_since", {})).items()
            },
        )


def _run_loads_to_list(
    samples: tuple[LoadSample, ...], on_since: datetime | None
) -> list[list[float]]:
    if on_since is None:
        return []
    return [[(sample.at - on_since).total_seconds(), sample.load_m2k] for sample in samples]


def _run_loads(value: object, on_since: datetime | None) -> tuple[LoadSample, ...]:
    """Read [seconds since on_since, load] pairs; drop them without a run to anchor them."""
    samples = []
    for item in _list(value):
        pair = _list(item)
        if len(pair) != 2:  # noqa: PLR2004 - a [seconds, load] pair
            raise _invalid(item, "a [seconds, load] pair")
        samples.append((_float(pair[0]), _float(pair[1])))
    if on_since is None:
        return ()
    return tuple(
        LoadSample(on_since + timedelta(seconds=seconds), load) for seconds, load in samples
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
