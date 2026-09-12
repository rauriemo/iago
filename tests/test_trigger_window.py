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

    decisions = []
    window = TriggerWindow(observe=lambda intent, decision: decisions.append(decision))
    assert window.start(core, {}, lambda: True, change_mode, 0.02)
    assert not window.start(core, {}, lambda: True, change_mode, 0.02)
    await asyncio.sleep(0)
    assert not window.starting and window.busy
    if response:
        core.user_revision += 1
    await window.task
    assert modes == (["conversation"] if response else ["conversation", "aware"])
    core.proactive.assert_awaited_once()
    assert decisions == [
        "window_starting",
        "greeting_scheduled",
        "window_promoted" if response else "window_timed_out",
    ]


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


@pytest.mark.features("P5", "P7", "D6")
@pytest.mark.scenario("TRIGGER-WINDOW-RESTORE-FAILURE")
@pytest.mark.parametrize("notification_fails", [False, True])
@pytest.mark.parametrize("idle_fails", [False, True])
async def test_restore_failure_is_observed_without_unhandled_background_task(
    notification_fails, idle_fails
):
    core = SimpleNamespace(
        mode="aware", user_revision=0, proactive=AsyncMock(return_value=True), emit=AsyncMock()
    )
    if notification_fails:
        core.emit.side_effect = RuntimeError("private transport details")

    async def change_mode(owner, mode):
        if mode == "aware":
            raise OSError("private provider details")
        if mode == "idle" and idle_fails:
            raise OSError("private idle cleanup details")
        owner.mode = mode

    window = TriggerWindow()
    window.start(core, {}, lambda: True, change_mode, 0)
    await window.task
    assert not window.busy and not window.starting
    assert window.error == "OSError"
    assert core.mode == ("conversation" if idle_fails else "idle")
    assert core.emit.await_count == (2 if idle_fails else 1)
    assert "private" not in str(core.emit.call_args)


@pytest.mark.features("P5", "P7", "C2", "D6")
@pytest.mark.scenario("TRIGGER-CLEANUP-BEFORE-NOTIFICATION")
@pytest.mark.parametrize("failure_phase", ["restore", "proactive"])
async def test_slow_error_delivery_does_not_delay_idle_cleanup(failure_phase):
    entered, release = asyncio.Event(), asyncio.Event()

    async def emit(*args, **kwargs):
        entered.set()
        await release.wait()

    core = SimpleNamespace(
        mode="aware", user_revision=0, proactive=AsyncMock(return_value=True), emit=emit
    )
    if failure_phase == "proactive":
        core.proactive.side_effect = OSError("synthetic greeting failure")
    modes = []

    async def change_mode(owner, mode):
        modes.append(mode)
        if mode == "aware" and failure_phase == "restore":
            raise OSError("synthetic restore failure")
        owner.mode = mode

    window = TriggerWindow()
    window.start(core, {}, lambda: True, change_mode, 0)
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert core.mode == ("idle" if failure_phase == "restore" else "aware")
        assert modes == (
            ["conversation", "aware", "idle"]
            if failure_phase == "restore"
            else ["conversation", "aware"]
        )
    finally:
        release.set()
        await window.task
