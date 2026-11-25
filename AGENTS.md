# AGENTS.md

Instructions for anyone, human or agent, changing this repository. CI enforces every rule marked
**(CI)**; a change that does not pass `uv run pre-commit run --all-files` and `uv run pytest --cov`
is not done.

## What this is

A Home Assistant custom integration (`custom_components/heating_controller`, domain
`heating_controller`) that decides when a boiler runs and how hard. It reads room thermostats, wall
sensors and radiator valves; it writes only the boiler switch, the boiler output number and a
heartbeat button. It never writes room targets or valve settings.

## Layout

```
custom_components/heating_controller/
  engine/      pure decision logic: snapshot types, room demand, load, state machine,
               modulation, guard. No Home Assistant imports. (CI: import-linter)
  adapter/     Home Assistant side: building the snapshot from states, writing commands,
               heartbeat, persistence, repairs, notifications
  *.py         integration entry points and platforms (config_flow, sensor, switch, button, …)
sim/           thermal simulator and history replay; drives the engine without Home Assistant
tests/         pytest; tests/engine needs no Home Assistant instance
```

Data flows one way: snapshot → engine core → engine guard → adapter. The guard always has the last
word and can only make things safer (stop, veto a start, clamp output).

## Python rules

- Python 3.14, same as Home Assistant 2026.11. Dependencies via `uv`; never `pip install` into the
  project. Pin dev dependencies exactly; bump `homeassistant` and `pytest-homeassistant` together.
- **Typing (CI: `mypy --strict`).** Every function, argument and return is annotated. No `Any`
  except at the Home Assistant boundary, and then with a comment saying why. No `# type: ignore`
  without an error code and a reason.
- **Lint and format (CI: ruff, `select = ["ALL"]`).** Add to the ignore list only with a comment
  explaining the rule conflict; prefer a local `# noqa: CODE` with a reason over a global ignore.
- **The engine is pure (CI: import-linter).** No `homeassistant` imports, no I/O, no clock reads:
  time comes in as an argument. Prefer frozen dataclasses and functions over classes with state.
- Home Assistant schemas use `probatio` (HA 2026.11 replaced voluptuous); use `@override` on
  overridden methods, `entry.runtime_data` for runtime state, `has_entity_name = True` and unique ids
  on every entity.
- Async code in the adapter only; never block the event loop.
- Names in English; units in names where ambiguous (`stale_after`, `area_m2`, `load_m2k`).

## Tests

- **No behaviour without a test.** A bug fix starts with a failing test.
- `engine/` and the guard: 100 % line and branch coverage. Everything else: ≥ 95 % per module
  (CI: coverage `fail_under`).
- Engine tests are plain pytest with fixed datetimes. Home Assistant tests use the
  `pytest-homeassistant` fixtures (`hass`, `MockConfigEntry`); do not add
  `pytest-homeassistant-custom-component`.
- A new safety rule gets a simulator scenario as well as unit tests.

## Simulator and replay

`sim/` drives the engine without Home Assistant. It may import the engine; nothing under
`custom_components/` may import `sim` (CI: import-linter).

```bash
uv run python -m sim.run --list                     # the scenarios
uv run python -m sim.run --scenario cold_night      # one scenario (--csv out.csv for the record)
uv run python -m sim.run --all                      # every scenario; exits 1 on any failed check
uv run python -m sim.tune [--quick]                 # compare modulation variants (minutes)
uv run python -m sim.replay sim/data/history.json --mapping sim/data/mapping.json [--csv out.csv]
```

- `plant.py` one thermal node per room, radiator lag, boiler with its 30 % floor;
  `devices.py` sensors (noise, quantisation, report interval/on change), Moes and Sonoff
  valves, VTherm, the boiler board with its heartbeat and API failsafes; `house.py` the default
  four-room house (made-up parameters).
- `loop.py` steps the plant, evaluates every 60 s, persists the engine state through
  `to_dict`/`from_dict` every step, and records each minute.
- `metrics.py` checks the spec's guard limits on the physical boiler and the success criteria;
  `scenarios.py` adds each scenario's own checks. A new safety rule needs a scenario here.
- `replay.py` reads Home Assistant's REST history export
  (`/api/history/period/<start>?filter_entity_id=…&minimal_response=false&significant_changes_only=false`)
  and a mapping file (example: `sim/examples/mapping.example.json`) and prints what the engine
  would have done next to what the boiler did.

Replay input is real history and stays in `sim/data/`, which is git-ignored; so do real mapping
files.

## Privacy

This repository is public. Never commit real entity ids from a specific home, chat ids, tokens,
host names, floor plans or exported history. Use neutral examples (`switch.boiler`,
`sensor.bedroom_temperature`) in code, tests and docs.

## Commits and releases

- Small commits; imperative subject line saying what changes and why (no `feat:` prefixes).
- `main` must stay green. Releases are git tags `vX.Y.Z` with a GitHub release; the tag must equal
  `version` in `manifest.json` (CI). Stay on `0.x` until the controller runs a real boiler.
- `manifest.json` keys: `domain`, `name`, then alphabetical (hassfest).
