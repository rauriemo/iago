"""Actual Astra handoff with synthetic controller observations and a text-only sink.

This qualifies provider input association, not live camera detection or acoustics.
"""

import asyncio
import copy
import time

import pytest

from reachy_brain.behavior.thumbs import Observation, Question
from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.providers.live import AstraBrain
from reachy_brain.vision.store import VisualStore


@pytest.mark.live_provider
@pytest.mark.features("P10", "C4")
@pytest.mark.scenario("P10-ASTRA-ASSOCIATED-YES-NO")
@pytest.mark.parametrize("gesture,value", [("thumb_up", "yes"), ("thumb_down", "no")])
async def test_astra_receives_one_question_bound_turn(gesture, value, live_gate, record_property):
    settings = Settings(thumb_responses_enabled=True)
    if not settings.openai_api_key.get_secret_value():
        pytest.skip("OPENAI_API_KEY missing in private setup")
    if settings.iago_development_budget == 0:
        pytest.skip("Live development budget is zero")
    requests, provider_events, emitted, spoken = [], [], [], []

    class ObservedAstra(AstraBrain):
        async def stream(self, messages, tools, **kwargs):
            requests.append(copy.deepcopy(messages))
            async for event in super().stream(messages, tools, **kwargs):
                provider_events.append(event)
                yield event

    class TextSink:
        async def feed(self, text):
            spoken.append(text)

    async def send(event):
        emitted.append(event)

    brain = ObservedAstra(settings, live_gate)
    executor = ToolExecutor(ToolRegistry(), ActionPolicy(), None)
    core = Conversation(settings, brain, {}, executor, VisualStore(), send)
    core.mode = "conversation"

    async def text_only_answer(epoch):
        await core._answer(epoch, TextSink())

    # Exercise the production model/tool loop without pretending to play audio.
    core.answer = text_only_answer
    core.history.extend(
        [
            {
                "role": "user",
                "content": "This is a fictional preference exercise, with no external action. "
                "Ask whether I prefer a blue dome. After my answer, reply with exactly "
                "BLUE ACCEPTED if yes or BLUE REJECTED if no.",
            },
            {"role": "assistant", "content": "Do you prefer a blue dome?"},
        ]
    )
    camera = core.visual.source(core.connection, "camera", "Synthetic provider fixture")
    at = time.time()
    question_id = "synthetic-blue-dome"
    core.thumbs.present(
        Question(question_id, core.session, core.epoch, camera.id, camera.generation, at, at + 15)
    )
    for index, (delta, observed) in enumerate(
        [(0, "neutral"), (0.31, "neutral"), (0.4, gesture), (0.8, gesture)]
    ):
        core.thumbs.observe(
            Observation(
                str(index),
                camera.id,
                camera.generation,
                "camera",
                at + delta,
                observed,
                0.95,
                1,
                True,
            ),
            now=at + delta,
        )
    response = core.thumbs.poll(now=at + 1.06)
    assert response is not None and response["value"] == value
    try:
        await core.accept_thumb(response)
        assert core.task is not None
        async with asyncio.timeout(60):
            await core.task
        await core.accept_thumb(response)
        await asyncio.sleep(0)
        assert len(requests) == 1
        associated = [
            row
            for row in requests[0]
            if row.get("role") == "user" and "[Live " in row.get("content", "")
        ]
        assert associated == [
            {
                "role": "user",
                "content": f"{value} [Live {gesture} response to question {question_id}]",
            }
        ]
        assert sum(event["type"] == "transcript" for event in emitted) == 1
        expected = "BLUE ACCEPTED" if value == "yes" else "BLUE REJECTED"
        assert "".join(spoken).strip().rstrip(".") == expected
        completed = [event for event in provider_events if event["type"] == "done"]
        assert len(completed) == 1
        assert completed[0]["provenance"]["requested_model"] == "gpt-6-astra"
        assert completed[0]["provenance"]["response_id"]
        assert len(live_gate.usage) == 1
        record_property("sample_count", 1)
        record_property(
            "measurements",
            {
                "fixture": "synthetic controller observations and text-only sink; actual Astra",
                "gesture": gesture,
                "associated_turns": len(associated),
                "provider_requests": len(requests),
                "duplicate_rejected": True,
                "answer": "".join(spoken),
                "provenance": completed[0]["provenance"],
                "physical_qualification": False,
            },
        )
    finally:
        await core.stop()
        await executor.close()
        await brain.close()
