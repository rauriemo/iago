"""Real API checks. Generated speech is synthetic and does not qualify real acoustics."""

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.operations import OperationStore
from reachy_brain.integrations.registry import (
    ActionPolicy,
    CallContext,
    ToolError,
    ToolExecutor,
    ToolRegistry,
)
from reachy_brain.integrations.runtime import IntegrationRuntime
from reachy_brain.providers.live import (
    AstraBrain,
    ElevenSpeech,
    OpenAISpeech,
    Transcription,
)
from reachy_brain.vision.store import VisualStore


@pytest.fixture
def live_settings():
    settings = Settings()
    if not settings.openai_api_key.get_secret_value():
        pytest.skip("OPENAI_API_KEY missing in private .env")
    if settings.iago_development_budget == 0:
        pytest.skip("Billable development not enabled")
    return settings


@pytest.mark.live_provider
@pytest.mark.features("C4", "C7")
@pytest.mark.scenario("PROVIDER-ASTRA-STREAM")
async def test_astra_stream(live_settings, live_gate):
    brain = AstraBrain(live_settings, live_gate)
    try:
        events = [
            event
            async for event in brain.stream(
                [{"role": "user", "content": "Reply with exactly: Iago ready."}], [], max_tokens=256
            )
        ]
        text = "".join(e["text"] for e in events if e["type"] == "text")
        assert "Iago ready" in text
        assert any(e["type"] == "done" for e in events)
        assert brain.settings.brain_model == "gpt-6-astra"
    finally:
        await brain.close()


@pytest.mark.live_provider
@pytest.mark.features("C1", "C6", "D6")
@pytest.mark.scenario("PROVIDER-OPENAI-SPEECH-TRANSCRIPTION")
async def test_synthetic_speech_transcription(live_settings, record_property, tmp_path, live_gate):
    gate = live_gate
    storage = gate.persistence
    voice, stt = OpenAISpeech(live_settings, gate), Transcription(live_settings, gate)
    try:
        pcm = b"".join(
            [chunk async for chunk in voice.stream("Iago can hear this synthetic test sentence.")]
        )
        assert 24000 < len(pcm) < 48000 * 20 and len(pcm) % 2 == 0
        await stt.start()
        for start in range(0, len(pcm), 9600):
            await stt.append(pcm[start : start + 9600])
        await stt.commit()
        async with asyncio.timeout(30):
            async for event in stt.events():
                if event["type"] == "conversation.item.input_audio_transcription.completed":
                    assert "synthetic" in event["transcript"].lower()
                    assert event["item_id"]
                    break
            else:
                pytest.fail("No completed transcription")
    finally:
        await stt.close()
        await voice.close()
        await storage.close()
    checkpoint = await asyncio.to_thread(storage.load)
    assert not checkpoint.active_attempts
    assert set(checkpoint.identities) == gate.seen_usage
    assert checkpoint.unknown_charges == gate.unknown_charges
    assert checkpoint.estimated_usd == gate.estimated_usd
    assert {r["provider"] for r in gate.usage} == {"openai_tts", "openai_stt"}
    assert all(r["usage"]["dispatched"] for r in gate.usage)
    row = next(r for r in gate.usage if r["provider"] == "openai_stt")
    usage = row["usage"]
    assert usage["completed_items"] == 1
    assert usage["sent_pcm_bytes"] == len(pcm)
    assert row["usage_priced_completely"] is False
    if usage["duration_usage_items"]:
        assert row["estimated_usd"] is not None
    record_property("sample_count", 1)
    record_property(
        "observed",
        json.dumps(
            {
                "fixture": "provider-generated synthetic speech; no physical playback",
                "sent_pcm_bytes": len(pcm),
                "duration_usage_items": usage["duration_usage_items"],
                "returned_duration_seconds": usage["returned_duration_seconds"],
                "token_usage_items": usage["token_usage_items"],
                "missing_usage_items": usage["missing_usage_items"],
                "estimated_usd": row["estimated_usd"],
                "usage_priced_completely": row["usage_priced_completely"],
                "checkpoint_revision": checkpoint.revision,
                "checkpoint_identities": len(checkpoint.identities),
                "checkpoint_active_attempts": len(checkpoint.active_attempts),
                "checkpoint_unknown_charges": checkpoint.unknown_charges,
                "checkpoint_estimated_usd": checkpoint.estimated_usd,
            }
        ),
    )


@pytest.mark.live_provider
@pytest.mark.features("C5")
@pytest.mark.scenario("PROVIDER-ELEVEN-CHOSEN-VOICE")
async def test_chosen_eleven_voice(live_settings, tmp_path, record_property, live_gate):
    gate = live_gate
    storage = gate.persistence
    voice = ElevenSpeech(live_settings, gate)
    try:
        validation = await voice.validate()
        if not validation["valid"]:
            pytest.skip("Chosen ElevenLabs voice prerequisite: " + validation["reason"])
        chunks = [chunk async for chunk in voice.stream("Iago voice connection test.")]
        assert sum(map(len, chunks)) > 24000
        assert sum(map(len, chunks)) % 2 == 0
    finally:
        await storage.close()
    checkpoint = await asyncio.to_thread(storage.load)
    assert not checkpoint.active_attempts
    assert set(checkpoint.identities) == gate.seen_usage
    assert checkpoint.unknown_charges == gate.unknown_charges
    assert checkpoint.estimated_usd == gate.estimated_usd
    assert len(gate.usage) == 2  # Validation PCM probe and the requested test phrase.
    assert all(r["provider"] == "elevenlabs_tts" and r["usage"]["dispatched"] for r in gate.usage)
    record_property("sample_count", 1)
    record_property(
        "measurements",
        {
            "fixture": "synthetic voice probe and phrase; no physical playback",
            "pcm_bytes": sum(map(len, chunks)),
            "synthesis_attempts": len(gate.usage),
            "checkpoint_revision": checkpoint.revision,
            "checkpoint_identities": len(checkpoint.identities),
            "checkpoint_active_attempts": len(checkpoint.active_attempts),
            "checkpoint_unknown_charges": checkpoint.unknown_charges,
            "checkpoint_estimated_usd": checkpoint.estimated_usd,
        },
    )


@pytest.mark.live_provider
@pytest.mark.features("C4", "E1")
@pytest.mark.scenario("E1-ASTRA-WORKFLOW-READ-DRAFT")
async def test_astra_installed_workflow_reads_and_drafts(live_settings, tmp_path, record_property, live_gate):
    """Actual Astra/controller/MCP, fake calendar and synthetic PCM sink; no physical claim."""
    core_path = Path("reachy_brain/core/conversation.py")
    before = hashlib.sha256(await asyncio.to_thread(core_path.read_bytes)).hexdigest()
    config = json.loads(
        await asyncio.to_thread(
            Path("examples/integrations.fake-calendar.json").read_text, encoding="utf-8"
        )
    )
    config["modules"][0]["command"] = sys.executable
    config["modules"][0]["args"][-1] = str(tmp_path / "fake.sqlite")
    installation = tmp_path / "installation.json"
    await asyncio.to_thread(installation.write_text, json.dumps(config), encoding="utf-8")
    registry, policy = ToolRegistry(), ActionPolicy()
    journal = OperationStore(tmp_path / "operations.sqlite")
    calls = []

    class ObservedExecutor(ToolExecutor):
        async def execute(self, key, payload, context):
            result = await super().execute(key, payload, context)
            calls.append((key, result))
            return result

    class SyntheticVoice:
        async def stream(self, text):
            yield bytes(960)

    executor = ObservedExecutor(registry, policy, journal)
    gate = live_gate
    brain = AstraBrain(live_settings, gate)
    messages = []

    async def send(message):
        messages.append(message)
        # Synthetic sink consumes each tiny PCM segment immediately. These
        # acknowledgments model conversation history only, never real acoustics.
        if message["type"] == "audio" and core.speech:
            core.speech.acknowledge(message["epoch"], message["sequence"])
        elif message["type"] == "segment_end":
            await core.heard(message["epoch"], message["segment"])

    core = Conversation(
        live_settings, brain, {"openai": SyntheticVoice()}, executor, VisualStore(), send
    )
    try:
        async with IntegrationRuntime(registry, policy, installation) as runtime:
            core.runtime_capabilities = lambda: frozenset(runtime.capabilities)
            core.mode = "conversation"
            for prompt, required in [
                (
                    "Discover the installed workflows with the workflow discovery tool and tell me their IDs. Do not write anything.",
                    {"workflows__local__discover"},
                ),
                (
                    "Load the installed fake-calendar-review workflow instructions, then read synthetic-a calendar availability. Do not draft or create yet.",
                    {"workflows__local__load", "calendar__synthetic-a__list_events"},
                ),
                (
                    "Prepare only a draft using draft_event for synthetic-a titled Iago isolated review at 2030-01-01T12:00:00Z. Do not create, send or schedule it.",
                    {"calendar__synthetic-a__draft_event"},
                ),
            ]:
                start = len(calls)
                await core.user_turn(prompt)
                await asyncio.wait_for(core.task, 90)
                assert required <= {
                    key for key, result in calls[start:] if result["status"] == "ok"
                }, json.dumps(
                    {
                        "tools": [(key, result["status"]) for key, result in calls[start:]],
                        "errors": [m.get("message") for m in messages if m["type"] == "error"],
                        "final": [m.get("text") for m in messages if m["type"] == "answer"],
                    }
                )
            assert not any(message["type"] in {"error", "confirmation"} for message in messages)
            assert not any(key.endswith("__create_event") for key, _ in calls)
            read = next(result for key, result in calls if key.endswith("__list_events"))
            assert read["result"]["events"] == []
            draft = next(result for key, result in calls if key.endswith("__draft_event"))
            assert "Iago isolated review" in json.dumps(draft["result"])
            final_store = await ToolExecutor.execute(
                executor,
                "calendar__synthetic-a__list_events",
                {},
                CallContext("verification", 0, capabilities=frozenset(runtime.capabilities)),
            )
            assert final_store["status"] == "ok" and final_store["result"]["events"] == []
            assert (
                hashlib.sha256(await asyncio.to_thread(core_path.read_bytes)).hexdigest() == before
            )
            record_property("sample_count", 3)
            record_property(
                "expected",
                "Actual Astra discovers, loads, reads and drafts through controller; no external write",
            )
            record_property(
                "observed",
                json.dumps(
                    {
                        "tools": [key for key, _ in calls],
                        "core_sha256": before,
                        "provider_requests": len(gate.usage),
                        "calendar": "isolated fake stdio",
                        "speech": "synthetic PCM sink",
                    }
                ),
            )
    finally:
        await core.stop()
        await executor.close()
        await brain.close()
        journal.close()


@pytest.fixture
async def live_calendar_installation(tmp_path, calendar_transport):
    config = json.loads(
        await asyncio.to_thread(
            Path("examples/integrations.fake-calendar.json").read_text, encoding="utf-8"
        )
    )
    module = config["modules"][0]
    module["command"] = sys.executable
    module["args"][-1] = str(tmp_path / "fake.sqlite")
    process = None
    try:
        if calendar_transport == "streamable-http":
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                *module["args"],
                "--transport",
                "streamable-http",
                "--port",
                str(port),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={
                    key: os.environ[key]
                    for key in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE")
                    if key in os.environ
                },
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            module["transport"] = calendar_transport
            module["url"] = f"http://127.0.0.1:{port}/mcp"
            async with asyncio.timeout(15), httpx.AsyncClient(trust_env=False, timeout=1) as client:
                while True:
                    assert process.returncode is None, "Fake HTTP calendar exited during startup"
                    try:
                        await client.get(module["url"])
                        break
                    except httpx.ConnectError:
                        await asyncio.sleep(0.05)
        yield config
    finally:
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()


@pytest.mark.live_provider
@pytest.mark.features("C4", "E1")
@pytest.mark.scenario("E1-ASTRA-CONTROLLED-WRITES")
@pytest.mark.parametrize("authorization", ["confirm", "allow"])
@pytest.mark.parametrize("calendar_transport", ["stdio", "streamable-http"])
async def test_astra_controlled_fake_calendar_writes(
    live_settings,
    tmp_path,
    record_property,
    authorization,
    calendar_transport,
    live_calendar_installation,
    live_gate,
):
    """Real Astra/controller/stdio writes; fake accounts, synthetic approval UI and PCM sink."""
    config = live_calendar_installation
    config["modules"][0]["tools"]["create_event"]["policy"] = authorization
    installation = tmp_path / "installation.json"
    await asyncio.to_thread(installation.write_text, json.dumps(config), encoding="utf-8")
    registry, policy = ToolRegistry(), ActionPolicy()
    journal = OperationStore(tmp_path / "operations.sqlite")
    executor = ToolExecutor(registry, policy, journal)
    gate = live_gate
    brain = AstraBrain(live_settings, gate)
    messages, proposals, titles = [], [], []
    expected_title = ""

    class SyntheticVoice:
        async def stream(self, text):
            yield bytes(960)

    async def store_events():
        result = await executor.execute(
            "calendar__synthetic-a__list_events",
            {},
            CallContext("verification", 0, capabilities=frozenset({"test_calendar"})),
        )
        assert result["status"] == "ok"
        return result["result"]["events"]

    async def send(message):
        messages.append(message)
        if message["type"] == "audio" and core.speech:
            core.speech.acknowledge(message["epoch"], message["sequence"])
        elif message["type"] == "segment_end":
            await core.heard(message["epoch"], message["segment"])
        elif message["type"] == "confirmation":
            proposal = message["proposal"]
            assert authorization == "confirm"
            assert proposal["tool"] == "calendar__synthetic-a__create_event"
            assert proposal["account"] == "synthetic-a"
            assert proposal["payload"]["title"] == expected_title
            assert proposal["payload"]["start"] == "2030-01-01T12:00:00Z"
            assert len(await store_events()) == len(titles)
            with pytest.raises(ToolError, match="invalid_confirmation"):
                executor.confirm(proposal["operation_id"], "wrong-binding", input_kind="action_ui")
            assert len(await store_events()) == len(titles)
            executor.confirm(proposal["operation_id"], proposal["binding"], input_kind="action_ui")
            core.confirmations[proposal["operation_id"]].set_result(True)
            proposals.append(proposal["operation_id"])

    core = Conversation(
        live_settings, brain, {"openai": SyntheticVoice()}, executor, VisualStore(), send
    )
    try:
        async with IntegrationRuntime(registry, policy, installation) as runtime:
            core.runtime_capabilities = lambda: frozenset(runtime.capabilities)
            core.mode = "conversation"
            for index in range(2):
                expected_title = f"Iago isolated {authorization} {index}"
                await core.user_turn(
                    f'Create exactly one event in synthetic-a with title "{expected_title}" '
                    'and start "2030-01-01T12:00:00Z" using the enabled create_event tool. '
                    "This is an isolated fake calendar. Submit the action to application policy; "
                    "the application handles any required exact-action confirmation."
                )
                await asyncio.wait_for(core.task, 90)
                titles.append(expected_title)
                events = await store_events()
                assert sorted(e["title"] for e in events) == sorted(titles), json.dumps(
                    {
                        "events": events,
                        "errors": [m.get("message") for m in messages if m["type"] == "error"],
                    }
                )
            assert len(proposals) == (2 if authorization == "confirm" else 0)
            assert not any(m["type"] == "error" for m in messages)
            operations = await asyncio.to_thread(journal.recent)
            assert len(operations) == 2 and all(o["status"] == "succeeded" for o in operations)
            record_property("sample_count", 2)
            record_property(
                "expected",
                "Two authorized fake mutations; exact approval or no prompt under standing authorization",
            )
            record_property(
                "observed",
                json.dumps(
                    {
                        "authorization": authorization,
                        "confirmations": len(proposals),
                        "fake_events": len(events),
                        "durable_outcomes": [o["status"] for o in operations],
                        "provider_requests": len(gate.usage),
                        "approval_and_speech": "synthetic",
                        "transport": "actual MCP " + calendar_transport,
                    }
                ),
            )
    finally:
        await core.stop()
        await executor.close()
        await brain.close()
        journal.close()
