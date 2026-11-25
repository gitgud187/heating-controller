"""Shared fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration from custom_components."""


# The fake plant's mocked services, for tests outside tests/adapter as well.
from .adapter.conftest import calls  # noqa: E402, F401
