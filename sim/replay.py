"""
Replay recorded Home Assistant history through the engine and compare with what happened.

    uv run python -m sim.replay sim/data/history.json --mapping sim/data/mapping.json
        [--csv out.csv] [--start ISO] [--end ISO]

Input: the JSON returned by Home Assistant's REST history endpoint
    /api/history/period/<start>?end_time=<end>&filter_entity_id=<ids>
        &minimal_response=false&significant_changes_only=false
a list with one list per entity of state objects (`entity_id`, `state`, `attributes`,
`last_changed`, `last_updated` and, on current versions, `last_reported`).

The mapping file (see `sim/examples/mapping.example.json`) says which entity plays which part.
A field source is an entity id (its state), `entity_id@attribute` (an attribute) or `=value` (a
constant, e.g. a valve setting that is not recorded).

For every minute the replay takes the last known state of each entity. History stores a row
per change, and only the time of the last report before the next change, so between a change
and that last report the reading is taken as freshly reported; after it, as reported then.
The engine runs open loop: it believes its own commands (the snapshot's boiler state is the
engine's, or unavailable when the recorded switch was), so its decisions can be compared with
the recorded switch and power level minute by minute.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

from custom_components.heating_controller.engine.config import EngineConfig
from custom_components.heating_controller.engine.evaluate import Evaluation, evaluate
from custom_components.heating_controller.engine.model import (
    Controls,
    RoomSnapshot,
    Snapshot,
    Thermostat,
    ThermostatMode,
    Valve,
)
from custom_components.heating_controller.engine.state import EngineState
from custom_components.heating_controller.engine.validity import Flag, Reading

from .metrics import SAFETY_STOPS

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

MINUTE = timedelta(minutes=1)
UNAVAILABLE = frozenset({"unavailable", "unknown", ""})
TRUTHY = frozenset({"on", "true", "open", "detected"})

# --- history ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Row:
    """One recorded state of an entity."""

    updated: datetime
    reported: datetime
    state: str
    attributes: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Observed:
    """An entity's value at a moment: None when unavailable, unknown or not yet recorded."""

    value: object
    last_reported: datetime


class History:
    """Every mapped entity's rows, sorted by time."""

    def __init__(self, rows: Mapping[str, list[Row]]) -> None:
        """Rows per entity id."""
        self.rows = {
            entity: sorted(items, key=lambda row: row.updated) for entity, items in rows.items()
        }
        self._times = {
            entity: [row.updated for row in items] for entity, items in self.rows.items()
        }

    @classmethod
    def from_export(cls, data: object) -> History:
        """Parse the REST history response (a list of lists of state objects)."""
        rows: dict[str, list[Row]] = {}
        for group in _list(data, "history"):
            for item in _list(group, "entity history"):
                entry = _dict(item, "state object")
                entity = _str(entry.get("entity_id"), "entity_id")
                updated = _time(entry.get("last_updated") or entry.get("last_changed"))
                reported_raw = entry.get("last_reported")
                reported = updated if reported_raw is None else max(updated, _time(reported_raw))
                rows.setdefault(entity, []).append(
                    Row(
                        updated=updated,
                        reported=reported,
                        state=str(entry.get("state", "")),
                        attributes=_dict(entry.get("attributes", {}), "attributes"),
                    )
                )
        return cls(rows)

    def span(self, entities: Sequence[str]) -> tuple[datetime, datetime]:
        """From when every entity is known to the last recorded change."""
        firsts = [self.rows[entity][0].updated for entity in entities if self.rows.get(entity)]
        lasts = [row.reported for entity in entities for row in self.rows.get(entity, [])[-1:]]
        if not firsts:
            msg = "the history contains none of the mapped entities"
            raise ValueError(msg)
        return max(firsts), max(lasts)

    def row_at(self, entity: str, now: datetime) -> Row | None:
        """Return the row in force at `now`."""
        times = self._times.get(entity)
        if not times:
            return None
        index = bisect.bisect_right(times, now) - 1
        return None if index < 0 else self.rows[entity][index]


@dataclass(frozen=True, slots=True)
class Source:
    """Where a field comes from: an entity's state or attribute, or a constant."""

    entity: str | None
    attribute: str | None = None
    constant: str | None = None

    @classmethod
    def parse(cls, text: str) -> Source:
        """`entity_id`, `entity_id@attribute` or `=constant`."""
        if text.startswith("="):
            return cls(entity=None, constant=text[1:])
        entity, _, attribute = text.partition("@")
        return cls(entity=entity, attribute=attribute or None)

    def observe(self, history: History, now: datetime) -> Observed:
        """Return the value at `now` and when it was last reported."""
        if self.entity is None:
            return Observed(self.constant, now)
        row = history.row_at(self.entity, now)
        if row is None:
            return Observed(None, now)
        reported = min(now, row.reported)
        if row.state in UNAVAILABLE:
            return Observed(None, reported)
        value = row.state if self.attribute is None else row.attributes.get(self.attribute)
        return Observed(value, reported)


def _number(observed: Observed) -> Reading:
    value = observed.value
    number: float | None = None
    if isinstance(value, int | float) and not isinstance(value, bool):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            number = None
    return Reading(number, observed.last_reported)


def _text(observed: Observed) -> str | None:
    return None if observed.value is None else str(observed.value)


def _truthy(observed: Observed) -> bool:
    value = observed.value
    if isinstance(value, bool):
        return value
    return str(value).lower() in TRUTHY


# --- mapping ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoomMapping:
    """Where one room's inputs come from."""

    name: str
    area_m2: float
    climate: str
    wall: Source
    target: Source
    window: Source
    valve_heating: Source
    position: Source | None


@dataclass(frozen=True, slots=True)
class EntityMap:
    """The whole mapping file."""

    rooms: tuple[RoomMapping, ...]
    outdoor: Source
    boiler_switch: str
    boiler_power: str | None
    pause: Source | None = None

    def entities(self) -> list[str]:
        """Every entity id the mapping reads."""
        sources = [self.outdoor, *(() if self.pause is None else (self.pause,))]
        for room in self.rooms:
            sources += [
                room.wall,
                room.target,
                room.window,
                room.valve_heating,
                *(() if room.position is None else (room.position,)),
            ]
        ids = {source.entity for source in sources if source.entity is not None}
        ids.update(room.climate for room in self.rooms)
        ids.add(self.boiler_switch)
        if self.boiler_power is not None:
            ids.add(self.boiler_power)
        return sorted(ids)


def parse_mapping(data: object) -> EntityMap:
    """Read the mapping JSON (see the module docstring and the example file)."""
    top = _dict(data, "mapping")
    rooms = []
    for item in _list(top.get("rooms"), "rooms"):
        room = _dict(item, "room")
        name = _str(room.get("name"), "room name")
        climate = _str(room.get("climate"), f"{name}: climate")
        valve = room.get("valve")
        heating_source = climate if valve is None else _str(valve, f"{name}: valve")
        position = room.get("position_sensor")
        area = room.get("area_m2")
        if isinstance(area, bool) or not isinstance(area, int | float):
            msg = f"{name}: area_m2 must be a number"
            raise ValueError(msg)  # noqa: TRY004 - a bad file is a value error to the caller
        rooms.append(
            RoomMapping(
                name=name,
                area_m2=float(area),
                climate=climate,
                wall=Source.parse(_str(room.get("wall_sensor"), f"{name}: wall_sensor")),
                target=Source.parse(_str(room.get("target", f"{climate}@temperature"), "target")),
                window=Source.parse(_str(room.get("window", f"{climate}@window_state"), "window")),
                valve_heating=Source.parse(
                    _str(room.get("valve_action", f"{heating_source}@hvac_action"), "valve_action")
                ),
                position=None
                if position is None
                else Source.parse(_str(position, f"{name}: position_sensor")),
            )
        )
    power = top.get("boiler_power")
    pause = top.get("pause")
    return EntityMap(
        rooms=tuple(rooms),
        outdoor=Source.parse(_str(top.get("outdoor"), "outdoor")),
        boiler_switch=_str(top.get("boiler_switch"), "boiler_switch"),
        boiler_power=None if power is None else _str(power, "boiler_power"),
        pause=None if pause is None else Source.parse(_str(pause, "pause")),
    )


# --- snapshot ---------------------------------------------------------------------------------


def _valve(room: RoomMapping, history: History, now: datetime) -> Valve:
    action = room.valve_heating.observe(history, now)
    heating = Flag(
        None if action.value is None else action.value == "heating", action.last_reported
    )
    if room.position is None:
        return Valve(heating=heating)
    return Valve(heating=heating, position=_number(room.position.observe(history, now)))


def _room(room: RoomMapping, history: History, now: datetime) -> RoomSnapshot:
    climate = Source(room.climate).observe(history, now)
    mode = {"heat": ThermostatMode.HEAT, "off": ThermostatMode.OFF}.get(str(climate.value))
    target = room.target.observe(history, now)
    return RoomSnapshot(
        name=room.name,
        area_m2=room.area_m2,
        thermostat=Thermostat(
            mode=mode,
            target=Reading(_number(target).value, climate.last_reported),
            window_open=_truthy(room.window.observe(history, now)),
        ),
        wall=_number(room.wall.observe(history, now)),
        valve=_valve(room, history, now),
    )


def build_snapshot(
    mapping: EntityMap, history: History, now: datetime, *, engine_on: bool
) -> Snapshot:
    """Build the engine's input at one minute from the recorded states."""
    switch = Source(mapping.boiler_switch).observe(history, now)
    return Snapshot(
        now=now,
        taken_at=now,
        complete=all(history.row_at(entity, now) is not None for entity in mapping.entities()),
        rooms=tuple(_room(room, history, now) for room in mapping.rooms),
        outdoor=_number(mapping.outdoor.observe(history, now)),
        boiler_on=None if switch.value is None else engine_on,
    )


# --- replay -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Minute:
    """One replayed minute: the engine's decision next to what was recorded."""

    time: datetime
    evaluation: Evaluation
    recorded_on: bool | None
    recorded_pct: float | None

    @property
    def engine_on(self) -> bool:
        """What the engine commanded (off when it commanded nothing)."""
        command = self.evaluation.command
        return command is not None and command.heat

    @property
    def engine_pct(self) -> int | None:
        """The engine's output request, None when nothing was commanded."""
        command = self.evaluation.command
        return None if command is None else command.modulation_pct


def replay(
    mapping: EntityMap,
    history: History,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    config: EngineConfig | None = None,
) -> Iterator[Minute]:
    """Evaluate every minute between start and end (default: the history's span)."""
    config = config or EngineConfig()
    first, last = history.span(mapping.entities())
    now = (start or first).replace(second=0, microsecond=0) + (
        MINUTE if start is None else timedelta()
    )
    stop = end or last
    stored = EngineState().to_dict()
    while now <= stop:
        state = EngineState.from_dict(stored)
        snapshot = build_snapshot(mapping, history, now, engine_on=state.boiler_on)
        paused = mapping.pause is not None and _truthy(mapping.pause.observe(history, now))
        evaluation = evaluate(snapshot, state, config, Controls(paused=paused))
        stored = evaluation.state.to_dict()
        switch = Source(mapping.boiler_switch).observe(history, now)
        power = (
            Reading(None, now)
            if mapping.boiler_power is None
            else _number(Source(mapping.boiler_power).observe(history, now))
        )
        yield Minute(
            time=now,
            evaluation=evaluation,
            recorded_on=None if switch.value is None else _truthy(switch),
            recorded_pct=power.value,
        )
        now += MINUTE


@dataclass
class Summary:
    """Counts over a replay."""

    minutes: int = 0
    engine_starts: int = 0
    engine_run_min: int = 0
    recorded_starts: int = 0
    recorded_run_min: int = 0
    disagree_min: int = 0
    engine_only_min: int = 0
    recorded_only_min: int = 0
    pct_differences: list[float] = field(default_factory=list)
    guard_stops: dict[str, int] = field(default_factory=dict)
    room_status_min: dict[str, dict[str, int]] = field(default_factory=dict)

    def add(self, minute: Minute, previous: Minute | None) -> None:
        """Account for one minute."""
        self.minutes += 1
        engine, recorded = minute.engine_on, bool(minute.recorded_on)
        was_engine = previous is not None and previous.engine_on
        was_recorded = previous is not None and bool(previous.recorded_on)
        self.engine_starts += engine and not was_engine
        self.recorded_starts += recorded and not was_recorded
        self.engine_run_min += engine
        self.recorded_run_min += recorded
        self.disagree_min += engine != recorded
        self.engine_only_min += engine and not recorded
        self.recorded_only_min += recorded and not engine
        if was_engine and not engine:
            code = minute.evaluation.reason.code
            if code in SAFETY_STOPS:
                self.guard_stops[code] = self.guard_stops.get(code, 0) + 1
        if (
            engine
            and recorded
            and minute.recorded_pct is not None
            and minute.engine_pct is not None
        ):
            self.pct_differences.append(minute.engine_pct - minute.recorded_pct)
        for room in minute.evaluation.rooms:
            counts = self.room_status_min.setdefault(room.name, {})
            counts[room.status] = counts.get(room.status, 0) + 1

    def lines(self) -> list[str]:
        """Return the printed summary."""
        diffs = self.pct_differences
        mean_abs = sum(abs(d) for d in diffs) / len(diffs) if diffs else 0.0
        out = [
            f"minutes replayed      {self.minutes}",
            f"starts                engine {self.engine_starts}, recorded {self.recorded_starts}",
            f"run minutes           engine {self.engine_run_min}, recorded {self.recorded_run_min}",
            (
                f"disagreement minutes  {self.disagree_min} "
                f"(engine only {self.engine_only_min}, recorded only {self.recorded_only_min})"
            ),
            (
                f"output while both on  mean |engine - recorded| {mean_abs:.1f} % "
                f"over {len(diffs)} min"
            ),
            "guard stops           "
            + (", ".join(f"{k} {v}" for k, v in sorted(self.guard_stops.items())) or "none"),
            "room status minutes",
        ]
        for room, counts in self.room_status_min.items():
            parts = ", ".join(f"{status} {count}" for status, count in sorted(counts.items()))
            out.append(f"  {room:<14} {parts}")
        return out


CSV_HEADER = (
    "time",
    "engine_heat",
    "engine_pct",
    "recorded_heat",
    "recorded_pct",
    "agree",
    "load_m2k",
    "reason",
    "message",
)


def _csv_row(minute: Minute) -> list[object]:
    recorded = "" if minute.recorded_on is None else int(minute.recorded_on)
    return [
        minute.time.isoformat(),
        int(minute.engine_on),
        "" if minute.engine_pct is None else minute.engine_pct,
        recorded,
        "" if minute.recorded_pct is None else minute.recorded_pct,
        int(minute.engine_on == bool(minute.recorded_on)),
        f"{minute.evaluation.load_m2k:.2f}",
        minute.evaluation.reason.code,
        minute.evaluation.reason.message,
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """Replay a history export and print the comparison."""
    parser = argparse.ArgumentParser(
        prog="python -m sim.replay", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("history", type=Path, help="REST history export (JSON)")
    parser.add_argument("--mapping", type=Path, required=True, help="mapping file (JSON)")
    parser.add_argument("--csv", type=Path, help="write the per-minute comparison here")
    parser.add_argument(
        "--start", type=datetime.fromisoformat, help="ISO time, default: first full data"
    )
    parser.add_argument("--end", type=datetime.fromisoformat, help="ISO time, default: last change")
    args = parser.parse_args(argv)

    history = History.from_export(json.loads(args.history.read_text(encoding="utf-8")))
    mapping = parse_mapping(json.loads(args.mapping.read_text(encoding="utf-8")))
    summary = Summary()
    previous: Minute | None = None
    writer = None
    handle = None
    if args.csv is not None:
        handle = args.csv.open("w", newline="", encoding="utf-8")
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
    try:
        for minute in replay(mapping, history, start=args.start, end=args.end):
            summary.add(minute, previous)
            if writer is not None:
                writer.writerow(_csv_row(minute))
            previous = minute
    finally:
        if handle is not None:
            handle.close()
    for line in summary.lines():
        print(line)
    return 0


# --- JSON helpers -----------------------------------------------------------------------------


def _list(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        msg = f"{what}: expected a list"
        raise ValueError(msg)  # noqa: TRY004 - a bad file is a value error to the caller
    return cast("list[object]", value)


def _dict(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"{what}: expected an object"
        raise ValueError(msg)  # noqa: TRY004 - a bad file is a value error to the caller
    return {str(key): item for key, item in cast("dict[object, object]", value).items()}


def _str(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        msg = f"{what}: expected a string"
        raise ValueError(msg)
    return value


def _time(value: object) -> datetime:
    moment = datetime.fromisoformat(_str(value, "timestamp"))
    if moment.tzinfo is None:
        msg = f"timestamp without a time zone: {value!r}"
        raise ValueError(msg)
    return moment


if __name__ == "__main__":
    sys.exit(main())
