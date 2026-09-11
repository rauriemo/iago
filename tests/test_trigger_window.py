"""Synthetic timing and recognition callbacks verify temporary-session ownership."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from reachy_brain.behavior.window import TriggerWindow


@pytest.mark.features("P5", "P6", "P7", "C2")
@pytest.mark.scenario("TRIGGER-WINDOW-OWNERSHIP")
@pytest.mark.parametrize("response", [False, True])
async def test_timeout_returns_aware_but_real_response_promotes(response):
    core = SimpleNamespace(
        mode="aware", user_revision=0, proactive=AsyncMock(return_value=True), emit=AsyncMock()
    )
    modes = []

    async def change_mode(owner, mode):
        modes.append(mode)
        owner.mode = mode

    window = TriggerWindow()
    assert window.start(core, {}, lambda: True, change_mode, 0.02)
    assert not window.start(core, {}, lambda: True, change_mode, 0.02)
    await asyncio.sleep(0)
    assert not window.starting and window.busy
    if response:
        core.user_revision += 1
    await window.task
    assert modes == (["conversation"] if response else ["conversation", "aware"])
    core.proactive.assert_awaited_once()


@pytest.mark.features("P5", "P7", "C2")
@pytest.mark.scenario("TRIGGER-CANCELED-STARTUP")
async def test_manual_end_cancels_startup_before_late_activation():
    entered = asyncio.Event()
    core = SimpleNamespace(mode="aware", user_revision=0, proactive=AsyncMock(), emit=AsyncMock())

    async def change_mode(owner, mode):
        entered.set()
        await asyncio.Event().wait()
        owner.mode = mode

    window = TriggerWindow()
    window.start(core, {}, lambda: True, change_mode, 30)
    await entered.wait()
    assert window.starting
    await window.cancel()
    core.mode = "idle"
    await asyncio.sleep(0)
    assert core.mode == "idle" and window.task.cancelled()
    core.proactive.assert_not_called()
