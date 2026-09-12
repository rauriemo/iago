"""Bounded provider-event ordering and duplicate protection with synthetic transcripts."""

import asyncio

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import ActionPolicy, ToolError, ToolExecutor, ToolRegistry
from reachy_brain.vision.store import VisualStore


def controller():
    sent = []

    async def send(event):
        sent.append(event)

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
        pass

    core.answer = answer
    return core, sent


@pytest.mark.features("C1", "C2", "D5")
@pytest.mark.scenario("RECOGNITION-ORDERING-BUFFER-LIMIT")
@pytest.mark.parametrize(
    "kind",
    ["input_audio_buffer.committed", "conversation.item.input_audio_transcription.completed"],
)
async def test_unmatched_provider_items_cannot_grow_without_bound(kind):
    core, _ = controller()
    for i in range(32):
        await core.transcription_event(dict(type=kind, item_id=str(i), transcript="Synthetic"))
    with pytest.raises(ToolError, match="recognition_input_limit"):
        await core.transcription_event(dict(type=kind, item_id="overflow", transcript="Synthetic"))
    assert len(set(core.input_order) | core.pending_input.keys()) == 32
    await core.set_mode("aware")
    assert not core.input_order and not core.pending_input and not core.recording_inputs
    await core.executor.close()


@pytest.mark.features("C1", "C2", "D5")
@pytest.mark.scenario("RECOGNITION-DUPLICATE-RESULTS")
async def test_repeated_ack_and_final_cannot_create_duplicate_turns():
    core, sent = controller()
    ack = dict(type="input_audio_buffer.committed", item_id="synthetic")
    final = dict(
        type="conversation.item.input_audio_transcription.completed",
        item_id="synthetic",
        transcript="One synthetic turn",
    )
    for event in (final, dict(final), ack, dict(ack), dict(final), dict(ack)):
        await core.transcription_event(event)
    await core.task
    assert len([event for event in sent if event["type"] == "transcript"]) == 1
    assert not core.input_order and not core.pending_input
    await core.executor.close()


@pytest.mark.features("C1", "D5")
@pytest.mark.scenario("RECOGNITION-INVALID-PAYLOAD-REJECTION")
@pytest.mark.parametrize(
    "update",
    [
        {"item_id": None},
        {"item_id": []},
        {"item_id": "x" * 129},
        {"transcript": None},
        {"transcript": "x" * 12001},
    ],
)
async def test_invalid_final_is_rejected_without_buffer_mutation(update):
    core, sent = controller()
    with pytest.raises(ToolError, match="invalid_recognition"):
        await core.transcription_event(
            dict(
                type="conversation.item.input_audio_transcription.completed",
                item_id="synthetic",
                transcript="safe",
            )
            | update
        )
    assert not core.pending_input and not core.input_order and not sent
    await core.executor.close()


@pytest.mark.features("C1", "C2", "D5")
@pytest.mark.scenario("RECOGNITION-CONFLICT-AND-INACTIVE-INPUT")
async def test_conflicting_pending_final_fails_and_inactive_callbacks_are_ignored():
    core, sent = controller()
    event = dict(
        type="conversation.item.input_audio_transcription.completed",
        item_id="synthetic",
        transcript="first",
    )
    await core.transcription_event(event)
    with pytest.raises(ToolError, match="conflicting_transcription_result"):
        await core.transcription_event(event | {"transcript": "changed"})
    assert core.pending_input["synthetic"] == "first"
    await core.set_mode("aware")
    sent.clear()
    await core.transcription_event(event)
    await core.transcription_event(dict(type="input_audio_buffer.committed", item_id="synthetic"))
    await asyncio.sleep(0)
    assert not core.pending_input and not core.input_order and not sent
    await core.executor.close()


@pytest.mark.features("C1", "D5")
@pytest.mark.scenario("RECOGNITION-SHARED-CAPACITY-AND-DEDUP-RETENTION")
async def test_shared_pending_capacity_and_bounded_consumed_ids():
    core, _ = controller()
    for i in range(32):
        kind = (
            "input_audio_buffer.committed"
            if i < 16
            else "conversation.item.input_audio_transcription.completed"
        )
        await core.transcription_event(dict(type=kind, item_id=str(i), transcript="Synthetic"))
    with pytest.raises(ToolError, match="recognition_input_limit"):
        await core.transcription_event(
            dict(type="input_audio_buffer.committed", item_id="overflow")
        )
    await core.set_mode("aware")
    core.mode = "conversation"
    for i in range(140):
        await core.transcription_event(dict(type="input_audio_buffer.committed", item_id=str(i)))
        await core.transcription_event(
            dict(
                type="conversation.item.input_audio_transcription.completed",
                item_id=str(i),
                transcript="Synthetic",
            )
        )
        await core.task
    assert len(core.recognized_inputs) == 128
    assert not core.pending_input and not core.input_order
    await core.set_mode("aware")
    assert not core.recognized_inputs
    await core.executor.close()
