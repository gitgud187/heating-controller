# Heating Controller

A Home Assistant integration that owns a boiler: whether it runs, how hard, and when it must stop.
It reads the rooms (thermostats, wall sensors, radiator valves) and never commands them.

> Status: early development (0.x). Not ready for use.

## What it does

- **Demand from rooms, not from valves.** A room asks for heat from its wall sensor; its valve may
  only veto (a closed valve for 15 minutes marks the room as blocked).
- **Load, not votes.** Rooms are weighted by floor area, so a small room cannot start a large boiler
  on its own, but a long-waiting room eventually will.
- **Weather compensation with room influence.** Output = a base from the outdoor temperature plus a
  PI correction on the area-weighted room error.
- **A guard that always wins.** Starts per hour, minimum off time, maximum run with lockout, stop when
  no valve is open, output limits, no start on missing data. Separate from the logic, separately tested.
- **Fails closed.** A room it cannot see is not a reason to heat. A heartbeat lets the boiler board
  switch off on its own if the integration stops.
- **Explains itself.** Every decision carries a reason ("waiting: minimum off 3 min left").

## Requirements

- Home Assistant 2026.11 or newer
- A boiler with an on/off switch entity and a numeric output entity (1–100 %)
- A heartbeat button on the boiler board (see below)
- Per room: a climate entity, a wall temperature sensor, and a radiator valve
  (Moes TRV801 or Sonoff TRVZB profiles are built in)

## Installation

Settings → Marketplace → Add from link → `gitgud187/heating-controller`, then add the integration
under Settings → Devices & services. The dashboard card is a separate repository:
[`heating-controller-card`](https://github.com/gitgud187/heating-controller-card).

## Heartbeat

The integration presses a button entity every 60 seconds. The board that drives the boiler should
switch heating off when it has not been pressed for 5 minutes, and start with heating off.

## Development

```bash
uv sync
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
uv run pytest --cov
```

See [AGENTS.md](AGENTS.md) for the rules this repository enforces.

## Licence

MIT
