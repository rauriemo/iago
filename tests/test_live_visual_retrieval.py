"""Real Astra using the application retrieval loop on synthetic archived images."""

import asyncio
import io
import time

import pytest
from PIL import Image, ImageDraw

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.builtins import register_builtins
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.providers.live import AstraBrain
from reachy_brain.storage.notes import Notes
from reachy_brain.vision.store import VisualStore


@pytest.mark.live_provider
@pytest.mark.features("C4", "V2", "V4", "V6", "E1")
@pytest.mark.scenario("ASTRA-ARCHIVED-VISUAL-TOOL-LOOP")
@pytest.mark.parametrize("reference", ["timestamp", "spoken_keyword"])
async def test_astra_retrieves_older_visual_evidence(tmp_path, record_property, reference, live_gate):
    settings = Settings()
    if not settings.openai_api_key.get_secret_value():
        pytest.skip("OPENAI_API_KEY missing in private setup")
    if settings.iago_development_budget == 0:
        pytest.skip("Live development budget is zero")
    assert settings.brain_model == "gpt-6-astra"
    visual = VisualStore()
    source = visual.source("synthetic", "camera", "Synthetic camera")
    now = time.time()
    frames = []
    for older in (True, False):
        raster = Image.new("RGB", (480, 320), "white")
        draw = ImageDraw.Draw(raster)
        if older:
            draw.polygon([(240, 40), (80, 280), (400, 280)], fill="green")
        else:
            draw.ellipse((120, 40, 360, 280), fill="blue")
        data = io.BytesIO()
        raster.save(data, format="PNG")
        frames.append(
            visual.add(source.id, 0, now - (180 if older else 1), visual.prepare(data.getvalue()))
        )
    registry, policy = ToolRegistry(), ActionPolicy()
    if reference == "spoken_keyword":
        visual.associate_speech(
            "I will call this keepsake",
            frames[0].captured,
            frames[0].captured + 0.5,
            {source.id: source.generation},
        )
    register_builtins(registry, policy, visual, None, Notes(tmp_path / "notes.sqlite"))
    calls, messages = [], []

    class ObservedExecutor(ToolExecutor):
        async def execute(self, key, payload, context):
            result = await super().execute(key, payload, context)
            calls.append({"tool": key, "payload": payload, "status": result["status"]})
            return result

    class SyntheticVoice:
        async def stream(self, text):
            yield bytes(960)

    async def send(message):
        messages.append(message)
        if message["type"] == "audio" and core.speech:
            core.speech.acknowledge(message["epoch"], message["sequence"])
        elif message["type"] == "segment_end":
            await core.heard(message["epoch"], message["segment"])

    gate = live_gate
    brain = AstraBrain(settings, gate)
    core = Conversation(
        settings,
        brain,
        {"openai": SyntheticVoice()},
        ObservedExecutor(registry, policy, None),
        visual,
        send,
    )
    core.mode = "conversation"
    core.selected_source = source.id
    try:
        reference_prompt = (
            f"Look back at the older image from this camera near Unix timestamp {frames[0].captured}. "
            if reference == "timestamp"
            else "Look back at the earlier image I called keepsake while speaking. "
        )
        await core.user_turn(
            reference_prompt
            + "Use the visual history tools to find and inspect that earlier image. "
            "What color and geometric shape did it show? Answer with only two lowercase English words, "
            "color then shape. The current camera image is different."
        )
        await asyncio.wait_for(core.task, 90)
        answers = [m["text"] for m in messages if m["type"] == "answer"]
        errors = [m.get("message") for m in messages if m["type"] == "error"]
        record_property("sample_count", 1)
        record_property(
            "measurements",
            {
                "fixture": "synthetic archived shapes; voice and sink synthetic; real Astra and tool loop",
                "frame_hashes": [frame.image_sha256 for frame in frames],
                "reference": reference,
                "speech_fixture": "synthetic interval keywords"
                if reference == "spoken_keyword"
                else None,
                "expected": "green triangle",
                "answers": answers,
                "calls": calls,
                "errors": errors,
                "model": settings.brain_model,
                "provider_requests": len(gate.usage),
                "evidence_ids": [
                    item.get("id")
                    for message in messages
                    if message["type"] == "evidence"
                    for item in message["frames"]
                ],
                "recorded_evidence": core.answer_metadata.get("evidence_refs", []),
            },
        )
        assert answers and answers[-1].strip().rstrip(".") == "green triangle"
        assert not errors
        if reference == "spoken_keyword":
            assert any(
                call["tool"] == "visual__session__search_visual_history"
                and "keepsake" in call["payload"].get("query", "").casefold()
                and call["status"] == "ok"
                for call in calls
            )
        assert any(
            call["status"] == "ok"
            and call["tool"]
            in {"visual__session__inspect_frames", "visual__session__inspect_region"}
            for call in calls
        )
        assert frames[0].id in {
            item.get("id")
            for message in messages
            if message["type"] == "evidence"
            for item in message["frames"]
        }
        assert frames[0].id in {
            item.get("id") for item in core.answer_metadata.get("evidence_refs", [])
        }
    finally:
        await core.stop()
        await brain.close()
