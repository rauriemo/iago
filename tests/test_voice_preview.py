"""Real preview controller with synthetic PCM; no audible-device claim."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import ToolError


@pytest.mark.features("C2", "C5", "C6", "C8", "D4")
@pytest.mark.scenario("VOICE-PREVIEW-CONTROLLER")
@pytest.mark.parametrize("outcome", ["complete", "failed", "stop", "replace"])
async def test_preview_uses_active_voice_without_brain_history_or_fallback(outcome):
    events = []
    entered, release = asyncio.Event(), asyncio.Event()
    fallback = Mock()
    calls = []

    class Voice:
        async def stream(self, text):
            calls.append(text)
            entered.set()
            if outcome in ("stop", "replace"):
                await release.wait()
            if outcome == "failed":
                raise RuntimeError("synthetic provider failure")
            yield bytes(960)

    async def send(event):
        events.append(event)

    core = Conversation(
        Settings(_env_file=None),
        Mock(),
        {"elevenlabs": Voice(), "openai": fallback},
        SimpleNamespace(cancel_pending=Mock()),
        None,
        send,
    )
    with pytest.raises(ToolError, match="start_conversation"):
        await core.preview_voice()
    assert not calls
    core.mode = "conversation"
    core.voice_provider = "elevenlabs"
    await core.preview_voice()
    first = core.task
    await entered.wait()
    first_epoch = core.epoch
    if outcome == "replace":
        await core.preview_voice()
        release.set()
        await core.task
    elif outcome == "stop":
        await core.stop()
        release.set()
    await first
    for event in events:
        if event["type"] == "segment_end":
            await core.heard(event["epoch"], event["segment"])
    await core.stop()
    assert list(core.history) == []
    assert core.speech is None
    assert not fallback.mock_calls and not core.brain.mock_calls
    if outcome in ("stop", "replace"):
        assert not [e for e in events if e["type"] == "audio" and e["epoch"] == first_epoch]
    if outcome == "failed":
        assert any(e["type"] == "voice_preview" and e["status"] == "failed" for e in events)
        assert not [e for e in events if e["type"] == "audio"]
    elif outcome != "stop":
        assert any(e["type"] == "audio" for e in events)
