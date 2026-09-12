"""Controller continuation order with explicit recognition barriers; no live speech claim."""

import asyncio

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("C1", "C2", "C3", "C8")
@pytest.mark.scenario("SPEECH-CONTINUATION-ANSWER-ADMISSION")
@pytest.mark.parametrize(
    "phase", ["speaking", "await_ack", "await_final", "final_first", "empty", "empty_after_stop"]
)
async def test_older_completion_cannot_answer_during_pending_continuation(phase):
    sent, answers = [], []

    async def send(event):
        sent.append(event)

    class Recognition:
        async def commit(self):
            return True

    core = Conversation(
        Settings(_env_file=None),
        None,
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )
    core.mode = "conversation"

    async def answer(epoch):
        answers.append([dict(row) for row in core.history])

    core.answer = answer

    async def ack(item):
        await core.transcription_event({"type": "input_audio_buffer.committed", "item_id": item})

    async def final(item, text):
        await core.transcription_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": item,
                "transcript": text,
            }
        )

    try:
        await core.speech_onset(100.0)
        await core.commit_recognition(Recognition(), capture_start=100.0, capture_end=101.0)
        await ack("first")
        await core.speech_onset(102.0)
        if phase not in {"speaking", "empty", "empty_after_stop"}:
            await core.commit_recognition(Recognition(), capture_start=102.0, capture_end=103.0)
        if phase in {"await_final", "final_first"}:
            await ack("second")
        if phase == "final_first":
            await final("second", "and include the second condition")
        await final("first", "I am describing the first condition")
        await asyncio.sleep(0)
        if phase != "final_first":
            assert answers == [], "Older transcript started an answer during pending speech"
            assert core.user_speaking
            if phase in {"speaking", "empty", "empty_after_stop"}:
                await core.commit_recognition(Recognition(), capture_start=102.0, capture_end=103.0)
            if phase in {"speaking", "empty", "empty_after_stop", "await_ack"}:
                await ack("second")
            if phase == "empty_after_stop":
                await core.stop()
            await final(
                "second", "" if phase.startswith("empty") else "and include the second condition"
            )
        await asyncio.sleep(0)
        if phase == "empty_after_stop":
            assert answers == []
            assert not core.user_speaking
            return
        if phase == "empty":
            assert len(answers) == 1 and len(answers[0]) == 1
            assert answers[0][0]["content"] == "I am describing the first condition"
            assert not core.user_speaking
            return
        assert answers == [
            [
                {"role": "user", "content": "I am describing the first condition"},
                {"role": "user", "content": "and include the second condition"},
            ]
        ]
        assert not core.user_speaking
        assert [m["text"] for m in sent if m["type"] == "transcript"] == [
            "I am describing the first condition",
            "and include the second condition",
        ]
    finally:
        await core.stop()
        await core.executor.close()


@pytest.mark.features("C1", "C2", "C3")
@pytest.mark.scenario("TRANSCRIPT-DELIVERY-EPOCH-FENCE")
@pytest.mark.parametrize("interrupt", ["stop", "speech_onset"])
async def test_stop_during_transcript_delivery_cannot_start_replacement_answer(interrupt):
    answers = []

    async def send(event):
        if event["type"] == "transcript":
            if interrupt == "stop":
                await core.stop()
            else:
                await core.speech_onset(102.0)

    core = Conversation(
        Settings(_env_file=None),
        None,
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )
    core.mode = "conversation"

    async def answer(epoch):
        answers.append(epoch)

    core.answer = answer
    try:
        await core.user_turn("Older completed input", kind="speech")
        await asyncio.sleep(0)
        assert answers == []
        assert core.user_speaking == (interrupt == "speech_onset")
    finally:
        await core.stop()
        await core.executor.close()
