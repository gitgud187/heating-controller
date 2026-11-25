"""Turn a simulator run into a Home Assistant history export, with neutral entity ids."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from sim.loop import SimResult

type StateObject = dict[str, object]


def _rows(entity: str, points: list[tuple[datetime, str, dict[str, object]]]) -> list[StateObject]:
    """One row per change; `last_reported` is the last minute the value was seen again."""
    rows: list[StateObject] = []
    previous: tuple[str, dict[str, object]] | None = None
    for moment, state, attributes in points:
        if previous == (state, attributes):
            rows[-1]["last_reported"] = moment.isoformat()
            continue
        previous = (state, attributes)
        stamp = moment.isoformat()
        rows.append(
            {
                "entity_id": entity,
                "state": state,
                "attributes": attributes,
                "last_changed": stamp,
                "last_updated": stamp,
                "last_reported": stamp,
            }
        )
    return rows


def export(result: SimResult) -> list[list[StateObject]]:
    """The REST history response for the example mapping's entities."""
    series: dict[str, list[tuple[datetime, str, dict[str, object]]]] = {}

    def add(entity: str, moment: datetime, state: str, **attributes: object) -> None:
        series.setdefault(entity, []).append((moment, state, attributes))

    for sample in result.samples:
        t = sample.time
        add("sensor.outdoor_temperature", t, f"{sample.outdoor_c:.1f}")
        add("switch.boiler", t, "on" if sample.heating else "off")
        add("number.boiler_power_level", t, str(sample.power_pct))
        for room in sample.rooms:
            name = room.name
            wall = "unavailable" if room.wall_c is None else f"{room.wall_c:.1f}"
            add(f"sensor.{name}_temperature", t, wall)
            add(
                f"climate.{name}",
                t,
                "heat",
                temperature=room.target_c,
                window_state="on" if room.window_open else "off",
            )
            action = "heating" if room.opening > 0 else "idle"
            if name == "living_room":
                add(f"climate.{name}_trv", t, "heat", hvac_action=action)
                add(f"binary_sensor.{name}_window", t, "on" if room.window_open else "off")
                add(f"number.{name}_trv_valve_opening_degree", t, "100")
                add(f"number.{name}_trv_valve_closing_degree", t, "100")
            else:
                add(f"climate.{name}_trv", t, "heat", hvac_action=action, preset_mode="manual")
                add(f"sensor.{name}_trv_position", t, str(round(room.opening * 100)))
    return [_rows(entity, points) for entity, points in series.items()]
