"""Explicitly requested live-provider fixtures; offline tests never read private settings."""

import pytest

from reachy_brain.config import Settings
from reachy_brain.providers.validation import validation_gate


@pytest.fixture
async def live_gate():
    async with validation_gate(Settings()) as gate:
        yield gate
