from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.heating_controller.engine.validity import (
    Flag,
    Reading,
    fresh_value,
    is_valid,
)

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def test_fresh_value_is_valid() -> None:
    assert is_valid(Reading(20.5, NOW - timedelta(minutes=5)), NOW)


def test_missing_value_is_invalid() -> None:
    assert not is_valid(Reading(None, NOW), NOW)


def test_value_at_the_stale_limit_is_invalid() -> None:
    assert not is_valid(Reading(20.5, NOW - timedelta(minutes=20)), NOW)


def test_custom_stale_limit() -> None:
    reading = Reading(20.5, NOW - timedelta(minutes=3))
    assert not is_valid(reading, NOW, stale_after=timedelta(minutes=2))


def test_fresh_value_returns_valid_value() -> None:
    assert fresh_value(Reading(20.5, NOW), NOW) == 20.5


def test_fresh_value_hides_stale_value() -> None:
    assert fresh_value(Flag(value=True, last_reported=NOW - timedelta(hours=1)), NOW) is None


def test_flag_false_is_a_valid_value() -> None:
    assert fresh_value(Flag(value=False, last_reported=NOW), NOW) is False
