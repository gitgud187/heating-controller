"""
The controller: evaluates the engine, writes the boiler, presses the heartbeat, persists.

It evaluates every 60 s and again when an input changes, at most every 10 s. Commands are sent
on every evaluation while they differ from what Home Assistant shows, which doubles as the
keep-alive after a board restart.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.const import ATTR_ENTITY_ID, STATE_ON
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_BOILER_SWITCH,
    CONF_HEARTBEAT,
    CONF_NOTIFY_SERVICE,
    CONF_POWER_LEVEL,
    DOMAIN,
    EVALUATE_INTERVAL,
    HEARTBEAT_INTERVAL,
    HISTORY_LENGTH,
    MIN_REEVALUATE_GAP,
    STARTUP_GRACE,
    STORAGE_VERSION,
)
from ..engine.evaluate import Command, Evaluation, evaluate
from ..engine.model import Controls
from ..engine.overheat import track_overheat
from ..engine.state import EngineState
from .problems import ProblemReporter, problems_of

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from ..engine.config import EngineConfig
    from .snapshot import SnapshotBuilder

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One evaluation, as kept for diagnostics."""

    at: datetime
    evaluation: Evaluation

    def as_dict(self) -> dict[str, Any]:
        """Plain data for the diagnostics download."""
        command = self.evaluation.command
        return {
            "at": self.at.isoformat(),
            "reasons": [
                {"code": r.code.value, "message": r.message} for r in self.evaluation.reasons
            ],
            "command": None
            if command is None
            else {"heat": command.heat, "modulation_pct": command.modulation_pct},
            "load_m2k": round(self.evaluation.load_m2k, 2),
            "rooms": {room.name: room.status.value for room in self.evaluation.rooms},
        }


@dataclass(slots=True)
class UserControls:
    """What the pause switch and the clear-lockout button have set."""

    paused: bool = False
    clear_lockout: bool = False


@dataclass(slots=True)
class _Timers:
    unsubscribe: list[CALLBACK_TYPE] = field(default_factory=list)
    pending: CALLBACK_TYPE | None = None


class HeatingController:
    """Owns the boiler for one config entry."""

    def __init__(  # noqa: PLR0913 - wiring
        self,
        hass: HomeAssistant,
        *,
        entry_id: str,
        plant: Mapping[str, Any],
        builder: SnapshotBuilder,
        config: EngineConfig,
        store: Store[dict[str, Any]] | None = None,
    ) -> None:
        """Wire the controller; nothing runs until `async_start`."""
        self.hass = hass
        self._plant = plant
        self._builder = builder
        self.config = config
        self._store = store or Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")
        self.state = EngineState()
        self.controls = UserControls()
        self.last: Evaluation | None = None
        self.overheating: frozenset[str] = frozenset()
        self._overheat_since: dict[str, datetime] = {}
        self.history: deque[HistoryEntry] = deque(maxlen=HISTORY_LENGTH)
        self.problems = ProblemReporter(hass, entry_id, plant.get(CONF_NOTIFY_SERVICE))
        self._listeners: list[Callable[[], None]] = []
        self._timers = _Timers()
        self._last_evaluated: datetime | None = None

    # --- lifecycle -----------------------------------------------------------------------

    async def async_start(self) -> None:
        """Restore the state, start the timers and the input listener, evaluate once."""
        stored = await self._store.async_load()
        if stored is not None:
            try:
                self.state = EngineState.from_dict(stored)
            except ValueError:
                _LOGGER.warning("Stored controller state unreadable; starting fresh")
        self.problems.hold_until = dt_util.utcnow() + STARTUP_GRACE
        timers = self._timers.unsubscribe
        timers.append(async_track_time_interval(self.hass, self._on_tick, EVALUATE_INTERVAL))
        timers.append(async_track_time_interval(self.hass, self._on_heartbeat, HEARTBEAT_INTERVAL))
        timers.append(
            async_track_state_change_event(self.hass, self._builder.watched, self._on_change)
        )
        await self.async_evaluate()
        await self._async_press_heartbeat()

    async def async_stop(self) -> None:
        """Stop timers and listeners, save the state, drop the issues."""
        for unsubscribe in self._timers.unsubscribe:
            unsubscribe()
        self._timers.unsubscribe.clear()
        if self._timers.pending is not None:
            self._timers.pending()
            self._timers.pending = None
        await self._store.async_save(self.state.to_dict())
        self.problems.clear()

    # --- listeners for entities ----------------------------------------------------------

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call `listener` after every evaluation; returns the remover."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    # --- controls ------------------------------------------------------------------------

    async def async_set_paused(self, *, paused: bool) -> None:
        """Pause or resume; takes effect at once."""
        self.controls.paused = paused
        await self.async_evaluate()

    async def async_clear_lockout(self) -> None:
        """End a lockout early; takes effect at once."""
        self.controls.clear_lockout = True
        await self.async_evaluate()

    # --- evaluation ----------------------------------------------------------------------

    async def _on_tick(self, _now: datetime) -> None:
        await self.async_evaluate()

    async def _on_heartbeat(self, _now: datetime) -> None:
        await self._async_press_heartbeat()

    @callback
    def _on_change(self, _event: Event[EventStateChangedData]) -> None:
        """Re-evaluate on an input change, at most every `MIN_REEVALUATE_GAP`."""
        if self._timers.pending is not None:
            return
        now = dt_util.utcnow()
        since = None if self._last_evaluated is None else now - self._last_evaluated
        delay = 0.0 if since is None else max(0.0, (MIN_REEVALUATE_GAP - since).total_seconds())
        self._timers.pending = async_call_later(self.hass, delay, self._on_pending)

    async def _on_pending(self, _now: datetime) -> None:
        self._timers.pending = None
        await self.async_evaluate()

    async def async_evaluate(self) -> Evaluation:
        """Run one evaluation and act on it."""
        now = dt_util.utcnow()
        controls = Controls(paused=self.controls.paused, clear_lockout=self.controls.clear_lockout)
        self.controls.clear_lockout = False
        evaluation = evaluate(self._builder.build(now), self.state, self.config, controls)
        self.state = evaluation.state
        self.last = evaluation
        self._last_evaluated = now
        self.history.append(HistoryEntry(now, evaluation))
        self._overheat_since, self.overheating = track_overheat(
            self._overheat_since, evaluation.rooms, now, self.config.overheat
        )
        if evaluation.command is not None:
            await self._async_write(evaluation.command)
        self._store.async_delay_save(self.state.to_dict, 5)
        await self.problems.async_update(
            problems_of(evaluation, self.overheating, self.problems.current), now
        )
        for listener in list(self._listeners):
            listener()
        return evaluation

    async def _async_write(self, command: Command) -> None:
        """Write the output first, then the switch, and only what differs."""
        power = self._plant[CONF_POWER_LEVEL]
        switch = self._plant[CONF_BOILER_SWITCH]
        if _number_state(self.hass, power) != command.modulation_pct:
            await self._async_call("number", "set_value", power, {"value": command.modulation_pct})
        switch_state = self.hass.states.get(switch)
        is_on = switch_state is not None and switch_state.state == STATE_ON
        if command.heat and not is_on:
            await self._async_call("switch", "turn_on", switch)
        elif not command.heat and is_on:
            await self._async_call("switch", "turn_off", switch)

    async def _async_press_heartbeat(self) -> None:
        await self._async_call("button", "press", self._plant[CONF_HEARTBEAT])

    async def _async_call(
        self, domain: str, service: str, entity_id: str, data: dict[str, Any] | None = None
    ) -> None:
        try:
            await self.hass.services.async_call(
                domain, service, {ATTR_ENTITY_ID: entity_id, **(data or {})}, blocking=True
            )
        except HomeAssistantError as err:
            _LOGGER.warning("%s.%s on %s failed: %s", domain, service, entity_id, err)


def _number_state(hass: HomeAssistant, entity_id: str) -> float | None:
    state = hass.states.get(entity_id)
    if state is None:
        return None
    try:
        return float(state.state)
    except ValueError:
        return None
