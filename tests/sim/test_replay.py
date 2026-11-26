from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from custom_components.heating_controller.engine.reasons import ReasonCode
from sim.loop import simulate
from sim.replay import (
    History,
    Observed,
    Source,
    Summary,
    _number,
    build_snapshot,
    main,
    parse_mapping,
    replay,
)
from sim.scenarios import cold_night

from .history import export

EXAMPLE = Path(__file__).parents[2] / "sim" / "examples" / "mapping.example.json"
T0 = datetime(2026, 1, 14, 12, 0, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def mapping_data() -> dict[str, object]:
    data: dict[str, object] = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    return data


def state(
    entity: str, value: str, at: datetime, reported: datetime | None = None, **attrs: object
) -> dict[str, object]:
    row: dict[str, object] = {
        "entity_id": entity,
        "state": value,
        "attributes": attrs,
        "last_changed": at.isoformat(),
        "last_updated": at.isoformat(),
    }
    if reported is not None:
        row["last_reported"] = reported.isoformat()
    return row


@pytest.fixture(scope="module")
def recorded() -> list[list[dict[str, object]]]:
    scenario = replace(cold_night(), duration=timedelta(hours=6))
    return export(simulate(scenario))


def test_replay_of_simulated_history_agrees(
    recorded: list[list[dict[str, object]]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = tmp_path / "history.json"
    history.write_text(json.dumps(recorded), encoding="utf-8")
    out = tmp_path / "replay.csv"
    assert main([str(history), "--mapping", str(EXAMPLE), "--csv", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "disagreement minutes  0 " in printed
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == 6 * 60 - 1
    assert all(row["agree"] == "1" for row in rows)


def test_replay_window_and_example_mapping(recorded: list[list[dict[str, object]]]) -> None:
    history = History.from_export(recorded)
    mapping = parse_mapping(mapping_data())
    start = datetime(2026, 1, 14, 19, 0, tzinfo=UTC)
    minutes = list(replay(mapping, history, start=start, end=start + 10 * MINUTE))
    assert [m.time for m in minutes] == [start + i * MINUTE for i in range(11)]
    assert all(m.evaluation.command is not None for m in minutes)
    assert {room.name for room in minutes[0].evaluation.rooms} == {
        "bathroom",
        "kitchen",
        "bedroom",
        "living_room",
    }
    snap = build_snapshot(mapping, history, start, engine_on=True)
    assert snap.complete
    assert snap.rooms[0].valve.position is not None  # a room with a position sensor
    assert snap.rooms[3].valve.position is None  # a room with only a heating flag
    assert snap.rooms[3].valve.heating is not None


def test_source_parsing_and_observation() -> None:
    history = History.from_export(
        [
            [
                state("sensor.a", "20.5", T0, T0 + 10 * MINUTE),
                state("sensor.a", "unavailable", T0 + 20 * MINUTE),
            ],
            [state("climate.b", "heat", T0, temperature=21.0)],
        ]
    )
    assert Source.parse("=7") == Source(entity=None, constant="7")
    assert Source.parse("climate.b@temperature") == Source("climate.b", "temperature")
    assert Source.parse("=7").observe(history, T0) == Observed("7", T0)
    assert Source.parse("sensor.a").observe(history, T0 - MINUTE) == Observed(None, T0 - MINUTE)
    assert Source.parse("sensor.a").observe(history, T0 + 5 * MINUTE) == Observed(
        "20.5", T0 + 5 * MINUTE
    )
    # after the last report, the reading ages
    assert Source.parse("sensor.a").observe(history, T0 + 15 * MINUTE).last_reported == (
        T0 + 10 * MINUTE
    )
    assert Source.parse("sensor.a").observe(history, T0 + 25 * MINUTE).value is None
    assert Source.parse("climate.b@temperature").observe(history, T0).value == 21.0
    assert Source.parse("sensor.none").observe(history, T0).value is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(21, 21.0), ("20.4", 20.4), ("on", None), (None, None), (True, None)],
)
def test_numbers(value: object, expected: float | None) -> None:
    assert _number(Observed(value, T0)).value == expected


def test_unavailable_boiler_and_missing_entities() -> None:
    mapping = parse_mapping(mapping_data())
    history = History.from_export([[state("switch.boiler", "unavailable", T0)]])
    snap = build_snapshot(mapping, history, T0, engine_on=True)
    assert snap.boiler_on is None
    assert not snap.complete
    assert all(room.wall.value is None for room in snap.rooms)


def test_pause_source_and_guard_stop_summary() -> None:
    data = mapping_data()
    data["pause"] = "input_boolean.pause"
    data["rooms"] = [data["rooms"][2]]  # type: ignore[index] # the bedroom from the example
    mapping = parse_mapping(data)
    rows = [
        [state("switch.boiler", "off", T0)],
        [state("number.boiler_power_level", "30", T0)],
        [state("sensor.outdoor_temperature", "0.0", T0, T0 + 60 * MINUTE)],
        [state("sensor.bedroom_temperature", "18.0", T0, T0 + 60 * MINUTE)],
        [state("climate.bedroom", "heat", T0, T0 + 60 * MINUTE, temperature=21.0)],
        [state("climate.bedroom_trv", "heat", T0, hvac_action="heating", preset_mode="manual")],
        [state("sensor.bedroom_trv_position", "80", T0)],
        [
            state("input_boolean.pause", "off", T0),
            state("input_boolean.pause", "on", T0 + 10 * MINUTE),
        ],
    ]
    history = History.from_export(rows)
    summary = Summary()
    previous = None
    for minute in replay(mapping, history, end=T0 + 20 * MINUTE):
        summary.add(minute, previous)
        previous = minute
    assert summary.engine_starts == 1
    assert summary.recorded_starts == 0
    assert summary.guard_stops == {ReasonCode.PAUSED: 1}
    assert summary.engine_only_min == summary.disagree_min > 0
    lines = summary.lines()
    assert any("paused 1" in line for line in lines)
    assert any(line.strip().startswith("bedroom") for line in lines)


def test_mapping_errors() -> None:
    data = mapping_data()
    rooms = data["rooms"]
    assert isinstance(rooms, list)
    no_valve = {
        key: value for key, value in rooms[0].items() if key not in {"valve", "position_sensor"}
    }
    plain = parse_mapping({**data, "rooms": [no_valve]}).rooms[0]
    assert plain.position is None
    assert plain.valve_heating.entity == plain.climate  # the thermostat stands in
    with pytest.raises(ValueError, match="area_m2"):
        parse_mapping({**data, "rooms": [{**rooms[0], "area_m2": "big"}]})
    with pytest.raises(ValueError, match="expected a string"):
        parse_mapping({**data, "outdoor": 3})
    with pytest.raises(ValueError, match="expected a list"):
        parse_mapping({**data, "rooms": {}})


def test_history_errors() -> None:
    with pytest.raises(ValueError, match="expected a list"):
        History.from_export({})
    with pytest.raises(ValueError, match="expected an object"):
        History.from_export([[1]])
    with pytest.raises(ValueError, match="time zone"):
        History.from_export(
            [[{"entity_id": "a.b", "state": "1", "last_updated": "2026-01-01T00:00:00"}]]
        )
    with pytest.raises(ValueError, match="none of the mapped"):
        History.from_export([]).span(["a.b"])


def test_boolean_window_and_no_power_entity() -> None:
    data = mapping_data()
    del data["boiler_power"]
    data["rooms"] = [data["rooms"][2]]  # type: ignore[index] # the bedroom from the example
    mapping = parse_mapping(data)
    assert mapping.boiler_power is None
    history = History.from_export(
        [
            [state("switch.boiler", "on", T0)],
            [state("climate.bedroom", "heat", T0, temperature=21.0, window_state=True)],
        ]
    )
    snap = build_snapshot(mapping, history, T0, engine_on=False)
    assert snap.rooms[0].thermostat.window_open
    minute = next(replay(mapping, history, start=T0, end=T0))
    assert minute.recorded_on is True
    assert minute.recorded_pct is None
