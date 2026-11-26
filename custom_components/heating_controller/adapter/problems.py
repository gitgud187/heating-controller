"""
Problems: one repair issue while a problem lasts, one notification per episode.

Every problem has its own key (kind plus room), so two problems never mask each other. A
problem that ends deletes its issue and re-arms; the next occurrence notifies again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import slugify

from ..const import DOMAIN
from ..engine.demand import RoomStatus
from ..engine.reasons import ReasonCode

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..engine.evaluate import Evaluation

_LOGGER = logging.getLogger(__name__)


class ProblemKind(StrEnum):
    """Every problem the controller reports; the value is the issue's translation key."""

    WALL_INVALID = "wall_invalid"
    THERMOSTAT_INVALID = "thermostat_invalid"
    VALVE_DATA_MISSING = "valve_data_missing"
    VALVE_UNHEALTHY = "valve_unhealthy"
    ROOM_BLOCKED = "room_blocked"
    ROOM_OVERHEATING = "room_overheating"
    LOCKOUT = "lockout"
    NO_OPEN_VALVE = "no_open_valve"
    OUTDOOR_UNAVAILABLE = "outdoor_unavailable"
    BOILER_UNAVAILABLE = "boiler_unavailable"
    ENTITY_MISSING = "entity_missing"


ROOM_PROBLEMS: dict[RoomStatus, ProblemKind] = {
    RoomStatus.WALL_INVALID: ProblemKind.WALL_INVALID,
    RoomStatus.THERMOSTAT_INVALID: ProblemKind.THERMOSTAT_INVALID,
    RoomStatus.VALVE_DATA_MISSING: ProblemKind.VALVE_DATA_MISSING,
    RoomStatus.VALVE_UNHEALTHY: ProblemKind.VALVE_UNHEALTHY,
    RoomStatus.BLOCKED: ProblemKind.ROOM_BLOCKED,
}

MESSAGES: dict[ProblemKind, str] = {
    ProblemKind.WALL_INVALID: "{room}: wall sensor unavailable or stale, room left out",
    ProblemKind.THERMOSTAT_INVALID: "{room}: thermostat unavailable, room left out",
    ProblemKind.VALVE_DATA_MISSING: "{room}: valve unavailable, room left out",
    ProblemKind.VALVE_UNHEALTHY: "{room}: valve in the wrong mode or settings, room left out",
    ProblemKind.ROOM_BLOCKED: "{room}: needs heat but its valve stays closed",
    ProblemKind.ROOM_OVERHEATING: "{room}: well above its target for a while; valve held open?",
    ProblemKind.LOCKOUT: "Boiler {message}",
    ProblemKind.NO_OPEN_VALVE: "Boiler {message}",
    ProblemKind.OUTDOOR_UNAVAILABLE: "Outdoor temperature unavailable, using the fallback",
    ProblemKind.BOILER_UNAVAILABLE: "Boiler entities unavailable, nothing commanded",
    ProblemKind.ENTITY_MISSING: "Configured entities not found: {entities}",
}

# Notified when they happen, shown as an issue until the next start.
LOCKOUT_REASONS = frozenset({ReasonCode.MAX_RUN, ReasonCode.NO_PROGRESS})


@dataclass(frozen=True, slots=True)
class Problem:
    """One active problem."""

    kind: ProblemKind
    room: str | None
    message: str

    @property
    def key(self) -> str:
        """Issue id: kind, plus the room for room problems."""
        return self.kind.value if self.room is None else f"{self.kind}_{slugify(self.room)}"


def problems_of(
    evaluation: Evaluation,
    overheating: frozenset[str],
    previous: dict[str, Problem],
    missing: tuple[str, ...] = (),
) -> dict[str, Problem]:
    """Every problem the evaluation shows. Event-like problems last until the boiler starts."""
    found: list[Problem] = []
    if missing:
        text = MESSAGES[ProblemKind.ENTITY_MISSING].format(entities=", ".join(sorted(missing)))
        found.append(Problem(ProblemKind.ENTITY_MISSING, None, text))
    for room in evaluation.rooms:
        kind = ROOM_PROBLEMS.get(room.status)
        if kind is not None:
            found.append(Problem(kind, room.name, MESSAGES[kind].format(room=room.name)))
        if room.name in overheating:
            kind = ProblemKind.ROOM_OVERHEATING
            found.append(Problem(kind, room.name, MESSAGES[kind].format(room=room.name)))
    if evaluation.modulation.outdoor_fallback:
        kind = ProblemKind.OUTDOOR_UNAVAILABLE
        found.append(Problem(kind, None, MESSAGES[kind]))
    if evaluation.command is None:
        kind = ProblemKind.BOILER_UNAVAILABLE
        found.append(Problem(kind, None, MESSAGES[kind]))
    found.extend(_boiler_events(evaluation, previous))
    return {problem.key: problem for problem in found}


def _boiler_events(evaluation: Evaluation, previous: dict[str, Problem]) -> list[Problem]:
    """Report a lockout while it lasts and a no-open-valve stop until the next start."""
    running = evaluation.command is not None and evaluation.command.heat
    events: list[Problem] = []
    reason = evaluation.reason
    lockout = previous.get(ProblemKind.LOCKOUT.value)
    if reason.code in LOCKOUT_REASONS:
        lockout = Problem(
            ProblemKind.LOCKOUT, None, MESSAGES[ProblemKind.LOCKOUT].format(message=reason.message)
        )
    if lockout is not None and evaluation.state.lockout_until is not None and not running:
        events.append(lockout)
    stop = previous.get(ProblemKind.NO_OPEN_VALVE.value)
    if reason.code is ReasonCode.NO_OPEN_VALVE and reason.message.startswith("stopped"):
        kind = ProblemKind.NO_OPEN_VALVE
        stop = Problem(kind, None, MESSAGES[kind].format(message=reason.message))
    if stop is not None and not running:
        events.append(stop)
    return events


class ProblemReporter:
    """
    Turns the set of active problems into repair issues and notifications.

    A problem is reported once it has lasted `delay`; one that clears up sooner never is.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        notify_target: str | None,
        delay: timedelta = timedelta(0),
    ) -> None:
        """Remember where to report and how long a problem must last."""
        self._hass = hass
        self._entry_id = entry_id
        self._notify = notify_target
        self._delay = delay
        self._first_seen: dict[str, datetime] = {}
        self.current: dict[str, Problem] = {}
        self.active: dict[str, Problem] = {}

    async def async_update(self, problems: dict[str, Problem], now: datetime) -> None:
        """Report problems that lasted long enough, delete the issues of ended ones."""
        self.current = problems
        self._first_seen = {key: self._first_seen.get(key, now) for key in problems}
        for key in self.active.keys() - problems.keys():
            ir.async_delete_issue(self._hass, DOMAIN, key)
        reported = {key: p for key, p in self.active.items() if key in problems}
        for key, problem in problems.items():
            if key in reported or now - self._first_seen[key] < self._delay:
                continue
            ir.async_create_issue(
                self._hass,
                DOMAIN,
                key,
                config_entry_id=self._entry_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=problem.kind.value,
                translation_placeholders={"room": problem.room or "", "message": problem.message},
            )
            await self._async_notify(problem, now)
            reported[key] = problem
        self.active = reported

    def clear(self) -> None:
        """Delete every issue this reporter created (on unload)."""
        for key in self.active:
            ir.async_delete_issue(self._hass, DOMAIN, key)
        self.active = {}
        self.current = {}
        self._first_seen = {}

    async def _async_notify(self, problem: Problem, now: datetime) -> None:
        """
        Send one message.

        The target is either a notify entity (`notify.send_message`, the current way, e.g. a
        Telegram chat) or a legacy notify service (`notify.<service>`).
        """
        if not self._notify:
            return
        message = f"Heating: {problem.message} ({now:%H:%M})"
        domain, _, name = self._notify.partition(".")
        if not name:
            domain, name = "notify", domain
        target = f"{domain}.{name}"
        if self._hass.states.get(target) is not None:
            service, data = "send_message", {"entity_id": target, "message": message}
        else:
            service, data = name, {"message": message}
        try:
            await self._hass.services.async_call(domain, service, data, blocking=True)
        except HomeAssistantError:
            _LOGGER.warning("Could not send the notification for %s", problem.key)
