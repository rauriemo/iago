"""Synthetic packets test cross-channel arrival order, without a billable provider."""

import base64

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.robot.client import ClockEstimate
from reachy_brain.robot.session import RobotSession
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("D2", "D3", "C1", "C2")
@pytest.mark.scenario("ROBOT-COMMIT-AUDIO-BARRIER")
async def test_early_control_marker_cannot_commit_before_its_audio():
    marker = {"type": "speech_end", "sequence": 2, "captured": 10}

    class Edge:
        clock = ClockEstimate()

        async def microphone(self):
            for seq in [1, 2, 3]:
                yield {
                    "gap": False,
                    "sequence": seq,
                    "rate": 24000,
                    "pcm": base64.b64encode(bytes(960)).decode(),
                    "speech_events": [marker] if seq >= 2 else [],
                }

    calls = []

    class STT:
        async def append(self, data):
            calls.append(("append", len(data)))

        async def commit(self):
            calls.append(("commit", 0))

    async def notify(message):
        pass

    settings = Settings(_env_file=None)
    core = Conversation(
        settings,
        None,
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        notify,
    )
    session = RobotSession(
        settings,
        None,
        core,
        notify,
        client_factory=lambda *args, **kwargs: Edge(),
    )
    session.stt = STT()
    await session.event(marker)
    assert calls == []
    await session.microphone()
    assert calls == [("append", 960), ("append", 960), ("commit", 0), ("append", 960)]
