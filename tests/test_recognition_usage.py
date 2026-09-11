"""Synthetic transcription transport exercises real lifecycle and private usage counters."""

import asyncio
import json

import pytest

from reachy_brain.config import Settings
from reachy_brain.providers.live import ProviderError, ProviderGate, Transcription
from reachy_brain.providers.recognition_usage import RecognitionUsage


@pytest.mark.features("C1", "C2", "D6")
@pytest.mark.scenario("RECOGNITION-SESSION-USAGE")
@pytest.mark.parametrize("outcome", ["closed", "send_failure", "plan_limit", "setup_failure"])
async def test_recognition_usage_lifecycle(monkeypatch, outcome):
    class Socket:
        closed = False
        appends = 0

        async def __aenter__(self):
            return self

        async def send(self, raw):
            if json.loads(raw)["type"] == "input_audio_buffer.append":
                self.appends += 1
                if outcome == "send_failure":
                    raise OSError("Private transport failure")

        async def recv(self):
            if outcome == "setup_failure":
                raise OSError("Private setup failure")
            return json.dumps({"type": "session.updated", "session": {"id": "session-test"}})

        async def close(self):
            self.closed = True

        async def messages(self):
            for item, usage in [
                ("a", {"type": "duration", "seconds": 0.2}),
                ("a", {"type": "duration", "seconds": 0.2}),
                (
                    "b",
                    {
                        "type": "tokens",
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "total_tokens": 12,
                        "input_token_details": {"audio_tokens": 8, "text_tokens": 2},
                    },
                ),
                ("c", {"type": "duration", "seconds": -1}),
            ]:
                yield json.dumps(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "item_id": item,
                        "content_index": 0,
                        "usage": usage,
                        "transcript": "Private transcription",
                    }
                )

        def __aiter__(self):
            return self.messages()

    socket = Socket()
    monkeypatch.setattr("reachy_brain.providers.live.connect", lambda *args, **kwargs: socket)
    settings = Settings(_env_file=None, iago_development_budget="unlimited")
    gate = ProviderGate(settings)
    stt = Transcription(settings, gate)
    if outcome == "setup_failure":
        with pytest.raises(OSError):
            await stt.start()
    else:
        await stt.start()
        if outcome == "plan_limit":
            gate.limit("openai")
        if outcome in {"send_failure", "plan_limit"}:
            with pytest.raises(OSError if outcome == "send_failure" else ProviderError):
                await stt.append(bytes(9600))
        else:
            await stt.append(bytes(9600))
            active = gate.active_usage()
            assert len(active) == 1 and active[0]["provider"] == "openai_stt"
            assert active[0]["counters"]["sent_pcm_bytes"] == 9600
            assert await stt.commit()
            assert not await stt.commit()
            assert len([event async for event in stt.events()]) == 4
    await stt.close()
    await stt.close()
    assert socket.closed and len(gate.usage) == 1
    assert gate.active_usage() == []
    row = gate.usage[0]
    assert row["provider"] == "openai_stt"
    if outcome == "closed":
        assert row["estimated_usd"] == pytest.approx(0.2 * 0.017 / 60)
    else:
        assert row["estimated_usd"] is None
    assert row["usage_priced_completely"] is False and gate.unknown_charges == 1
    usage = row["usage"]
    assert usage["billing_usage_complete"] is False
    assert usage["status"] == ("disconnected" if outcome == "closed" else "failed")
    assert usage["sent_pcm_bytes"] == (9600 if outcome == "closed" else 0)
    assert usage["attempted_pcm_bytes"] == (0 if outcome == "setup_failure" else 9600)
    assert usage["sent_commits"] == (1 if outcome == "closed" else 0)
    if outcome == "closed":
        assert usage["completed_items"] == 3
        assert usage["missing_usage_items"] == 1
        assert usage["returned_duration_seconds"] == 0.2
        assert usage["returned_input_tokens"] == 10
        assert usage["returned_audio_tokens"] == 8
        assert usage["returned_output_tokens"] == 2
    if outcome == "plan_limit":
        assert socket.appends == 0
    assert usage["request_id_origin"] == (
        "local_session" if outcome == "setup_failure" else "provider_session"
    )
    assert "Private" not in json.dumps(row)


@pytest.mark.features("C2", "D6")
@pytest.mark.scenario("RECOGNITION-CANCEL-SETUP")
async def test_canceled_setup_has_one_unknown_attempt(monkeypatch):
    entered = asyncio.Event()

    class Connection:
        async def __aenter__(self):
            entered.set()
            await asyncio.Future()

    monkeypatch.setattr("reachy_brain.providers.live.connect", lambda *args, **kwargs: Connection())
    settings = Settings(_env_file=None, iago_development_budget="unlimited")
    gate = ProviderGate(settings)
    stt = Transcription(settings, gate)
    task = asyncio.create_task(stt.start())
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await stt.close()
    assert len(gate.usage) == 1
    assert gate.usage[0]["usage"]["status"] == "interrupted"
    assert gate.usage[0]["usage"]["attempted_pcm_bytes"] == 0


@pytest.mark.features("D6")
@pytest.mark.scenario("RECOGNITION-USAGE-BOUNDS")
def test_usage_missing_details_capacity_and_finalization():
    settings = Settings(_env_file=None, iago_development_budget="unlimited")
    gate = ProviderGate(settings)
    usage = RecognitionUsage(gate, settings.stt_model)
    usage.completed(
        {
            "item_id": "partial",
            "content_index": 0,
            "usage": {
                "type": "tokens",
                "input_tokens": 4,
                "output_tokens": 1,
                "total_tokens": 5,
                "input_token_details": {"audio_tokens": None},
            },
        }
    )
    assert usage.counts["returned_input_tokens"] == 4
    assert usage.counts["missing_token_detail_items"] == 1
    usage.seen = {(str(i), 0) for i in range(10000)}
    with pytest.raises(ValueError, match="capacity"):
        usage.completed({"item_id": "overflow", "content_index": 0})
    assert len(usage.seen) == 10000
    usage.finish()
    usage.add("sent_pcm_bytes", 960)
    usage.finish()
    assert len(gate.usage) == 1
    assert gate.usage[0]["usage"]["sent_pcm_bytes"] == 0
