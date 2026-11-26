# Heating Controller

A Home Assistant integration that owns a boiler: whether it runs, how hard, and when it must stop.
It reads the rooms (thermostats, wall sensors, radiator valves) and never commands them.

> Status: early development (0.x). Not ready for use.

## What it does

- **Demand from rooms, not from valves.** A room asks for heat from its wall sensor; its valve may
  only veto (a valve closed for 30 minutes while the room is at least 0.5 K below target
  marks it as blocked).
- **Load, not votes.** Rooms are weighted by floor area, so a small room cannot start a large boiler
  on its own, but a long-waiting room eventually will.
- **Weather compensation with room influence.** Output = a base from the outdoor temperature plus a
  PI correction on the area-weighted room error.
- **A guard that always wins.** Starts per hour, minimum off time, a lockout when after 3 hours
  no room is getting closer to its target and for any run at 6 hours, stop when no valve is open,
  output limits, no start on missing data. Separate from the logic, separately tested.
- **Fails closed.** A room it cannot see is not a reason to heat. A heartbeat lets the boiler board
  switch off on its own if the integration stops.
- **Explains itself.** Every decision carries a reason ("waiting: minimum off 3 min left").

## Requirements

- Home Assistant 2026.11 or newer
- A boiler with an on/off switch entity and a numeric output entity (1–100 %)
- A heartbeat button on the boiler board (see below)
- Per room: a thermostat (any climate entity that drives the radiator valve, for example a
  Better Thermostat) and a wall temperature sensor; optionally the valve's own climate entity
  and its position sensor

## Installation

Settings → Marketplace → Add from link → `gitgud187/heating-controller`, then add the integration
under Settings → Devices & services. The dashboard card is a separate repository:
[`heating-controller-card`](https://github.com/gitgud187/heating-controller-card).

## Configuration

1. **Boiler:** the demand switch, the output number (1–100 %), the board's heartbeat button, the
   outdoor temperature and, optionally, a notification service.
2. **Rooms:** on the integration page, *Add room* once per room: thermostat, wall sensor, floor
   area and, optionally, the valve (its heating state is read) and a valve position sensor.
   Without them the thermostat's own heating state tells whether the valve is open.
3. **Tuning:** *Configure* holds the thresholds and gains. The defaults are a reasonable start.

The controller exposes the boiler state with the reason for it, load, output and its terms,
starts per hour, run time and lockout; per room its status, demand, valve and load share. A
**Pause** switch holds the boiler off, **Clear lockout** ends a lockout early. Problems show up
as repair issues and, if configured, as one notification per episode.

## Heartbeat

The integration presses a button entity every 60 seconds. The board that drives the boiler should
switch heating off when it has not been pressed for 5 minutes, and start with heating off.

## Development

```bash
uv sync
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
uv run pytest --cov
uv run python -m sim.run --all   # thermal simulator scenarios
```

See [AGENTS.md](AGENTS.md) for the rules this repository enforces.

## Licence

MIT
