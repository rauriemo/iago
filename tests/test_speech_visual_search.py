"""Synthetic speech timing and real recognition ordering/visual store; no provider claim."""

import time

import pytest
from test_spoken_reference import STT, controller, prepared_image


@pytest.mark.features("V4", "V5", "V6", "C1")
@pytest.mark.scenario("SPEECH-VISUAL-INTERVAL-ORDERING")
@pytest.mark.parametrize("clear", [False, True])
async def test_reordered_transcripts_keep_interval_and_source_generation(clear):
    core, frames, turns, events = controller()
    source = core.visual.sources[frames[0].source]
    later = core.visual.add(source.id, 0, time.time() - 10, prepared_image())
    for frame in (frames[0], later):
        await core.speech_onset(frame.captured)
        await core.commit_recognition(STT(), capture_end=frame.captured + 0.1)
    for item in ("a", "b"):
        await core.transcription_event({"type": "input_audio_buffer.committed", "item_id": item})
    if clear:
        core.visual.clear(source.id)
        core.visual.add(source.id, source.generation, frames[0].captured, prepared_image())
        core.visual.add(source.id, source.generation, later.captured, prepared_image())
    await core.transcription_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "b",
            "transcript": "sapphire",
        }
    )
    assert core.visual.search(query="sapphire")["frames"] == []
    await core.transcription_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "a",
            "transcript": "emerald",
        }
    )
    if core.task:
        await core.task
    first = core.visual.search(query="emerald")["frames"]
    second = core.visual.search(query="sapphire")["frames"]
    if clear:
        assert not first and not second
    else:
        assert frames[0].id in {f["id"] for f in first}
        assert later.id not in {f["id"] for f in first}
        assert [f["id"] for f in second] == [later.id]
        assert "sapphire" not in str(second)  # Keyword index is not returned as visual fact.
        assert later.speech_metadata_bytes > 0
        core.visual.clear(source.id)
        assert core.visual.search(query="sapphire")["frames"] == []
    assert not core.speech_source_commits and not core.speech_source_inputs


@pytest.mark.features("V3", "V4", "V6")
@pytest.mark.scenario("SPEECH-VISUAL-PIN-BYTE-LIMIT")
def test_keyword_enrichment_cannot_exceed_shared_pin_budget():
    from reachy_brain.vision.store import VisualStore

    store = VisualStore(clock=lambda: 100)
    source = store.source("synthetic", "camera", "Camera")
    frames = [store.add(source.id, 0, at, prepared_image()) for at in (98, 99)]
    for frame in frames:
        store.pin(frame.id, "Explicit pin")
    initial = store.totals()["pin_bytes"]
    store.pin_bytes = initial + len('["emerald"]')
    store.associate_speech("emerald", 98, 99, {source.id: source.generation})
    assert store.totals()["pin_bytes"] <= store.pin_bytes
    assert frames[0].speech_terms == ["emerald"]
    assert frames[1].speech_terms == []
    assert store.describe(frames[1])["speech_index_limited"]
    assert [f["id"] for f in store.search(query="emerald")["frames"]] == [frames[0].id]
    assert store.get(frames[1].id).pin == "Explicit pin"
    store.unpin(frames[1].id)
    store.associate_speech("emerald", 98, 99, {source.id: source.generation})
    assert frames[1].speech_terms == ["emerald"]
    assert not store.describe(frames[1])["speech_index_limited"]
    store.max_bytes = frames[1].size - 1
    store.expire()
    assert frames[1].id not in store.frames
    assert frames[0].id in store.frames


@pytest.mark.features("V5", "V6")
@pytest.mark.scenario("SPEECH-VISUAL-UNKNOWN-CAPTURE-TIME")
def test_unknown_capture_time_cannot_establish_speech_association():
    from reachy_brain.vision.store import VisualStore

    store = VisualStore(clock=lambda: 100)
    source = store.source("synthetic", "camera", "Camera")
    known = store.add(source.id, 0, 98, prepared_image())
    unknown = store.add(source.id, 0, 99, prepared_image())
    unknown.capture_time_known = False
    unknown.timing_note = "Receipt time only; exposure timing unavailable"
    store.associate_speech("emerald", 98, 99, {source.id: source.generation})
    assert known.speech_terms == ["emerald"]
    assert unknown.speech_terms == []
    assert unknown.speech_metadata_bytes == 0
    assert [frame["id"] for frame in store.search(query="emerald")["frames"]] == [known.id]
    assert store.get(unknown.id) is unknown
    assert store.describe(unknown)["capture_time_known"] is False


@pytest.mark.features("V5", "V6", "C1", "C9")
@pytest.mark.scenario("SPEECH-CLOCK-UNCERTAINTY-ASSOCIATION")
async def test_commit_bound_survives_recognition_and_expands_association():
    core, frames, turns, events = controller()
    source = core.visual.sources[frames[0].source]
    at = time.time() - 5
    candidate = core.visual.add(source.id, 0, at - 4, prepared_image())
    await core.speech_onset(at)
    await core.commit_recognition(STT(), capture_end=at + 0.1, capture_clock_uncertainty=3)
    await core.transcription_event({"type": "input_audio_buffer.committed", "item_id": "a"})
    await core.transcription_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "a",
            "transcript": "emerald",
        }
    )
    await core.task
    assert core.turn_visual_interval["capture_clock_uncertainty"] == 3
    assert core.turn_visual_interval["capture_start"] == at
    assert [f["id"] for f in core.visual.search(query="emerald")["frames"]] == [candidate.id]
    await core.stop()
    await core.executor.close()


@pytest.mark.features("V5", "C1")
@pytest.mark.scenario("SPEECH-CLOCK-UNCERTAINTY-VALIDATION")
@pytest.mark.parametrize("bound", [True, -1, 11, float("nan"), float("inf"), "private"])
async def test_invalid_commit_bound_does_not_dispatch(bound):
    from reachy_brain.integrations.registry import ToolError

    core, frames, turns, events = controller()
    with pytest.raises(ToolError, match="invalid_speech_clock_uncertainty"):
        await core.commit_recognition(STT(), capture_clock_uncertainty=bound)
    assert not core.recording_commits
    await core.executor.close()
