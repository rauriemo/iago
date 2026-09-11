"""Synthetic SDK streams exercise real Astra attempt accounting without billable calls."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openai.types.responses.response_usage import ResponseUsage

from reachy_brain.config import Settings
from reachy_brain.providers.live import AstraBrain, ProviderError, ProviderGate


@pytest.mark.features("C1", "C2", "D6")
@pytest.mark.scenario("ASTRA-ATTEMPT-OUTCOMES")
@pytest.mark.parametrize(
    "outcome", ["completed", "failed", "incomplete", "eof", "interrupted", "transport"]
)
@pytest.mark.parametrize("returned_usage", [False, True])
async def test_astra_terminal_usage_and_interrupted_identity(monkeypatch, outcome, returned_usage):
    usage = ResponseUsage(
        input_tokens=100,
        input_tokens_details={"cached_tokens": 0, "cache_write_tokens": 0},
        output_tokens=10,
        output_tokens_details={"reasoning_tokens": 2},
        total_tokens=110,
    )

    class Stream:
        closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

        async def messages(self):
            yield SimpleNamespace(
                type="response.created", response=SimpleNamespace(id="response-test", usage=None)
            )
            yield SimpleNamespace(type="response.output_text.delta", delta="Private synthetic text")
            if outcome == "transport":
                raise OSError("Synthetic private transport detail")
            if outcome == "eof":
                return
            yield SimpleNamespace(
                type="response." + outcome,
                response=SimpleNamespace(
                    id="response-test",
                    usage=usage if returned_usage else None,
                    service_tier="default",
                ),
            )

        def __aiter__(self):
            return self.messages()

    transport = Stream()
    create = AsyncMock(return_value=transport)
    monkeypatch.setattr(
        "reachy_brain.providers.live.AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(
            responses=SimpleNamespace(create=create), close=AsyncMock()
        ),
    )
    settings = Settings(
        _env_file=None, openai_api_key="synthetic", iago_development_budget="unlimited"
    )
    gate = ProviderGate(settings)
    brain = AstraBrain(settings, gate)
    stream = brain.stream([{"role": "user", "content": "Private synthetic prompt"}], [])
    assert (await anext(stream))["type"] == "text"
    active = gate.active_usage()
    assert len(active) == 1 and active[0]["provider"] == "astra"
    assert active[0]["counters"]["received_text_characters"] == len("Private synthetic text")
    assert "Private synthetic" not in json.dumps(active)
    if outcome == "interrupted":
        await stream.aclose()
    elif outcome == "completed":
        assert (await anext(stream))["type"] == "done"
        # Closing immediately after done must not relabel the provider's completed attempt.
        assert len(gate.usage) == 1
        await stream.aclose()
    else:
        with pytest.raises(OSError if outcome == "transport" else ProviderError):
            await anext(stream)
    assert transport.closed and create.await_count == 1
    assert gate.active_usage() == []
    assert len(gate.usage) == 1
    row = gate.usage[0]
    assert row["usage"]["attempt_id"] == active[0]["attempt_id"]
    assert row["request_id"] == "response-test"
    assert row["usage"]["status"] == {"eof": "incomplete", "transport": "failed"}.get(
        outcome, outcome
    )
    known = returned_usage and outcome in {"completed", "failed", "incomplete"}
    assert row["usage"]["usage_returned"] is known
    assert row["estimated_usd"] == (0.0015 if known else None)
    assert gate.unknown_charges == (0 if known else 1)
    assert row["usage"]["request_id_origin"] == "provider"
    assert "Private synthetic" not in json.dumps(row)
    assert "transport detail" not in json.dumps(row)
    assert create.call_args.kwargs["model"] == "gpt-6-astra"


@pytest.mark.features("C2", "D6")
@pytest.mark.scenario("ASTRA-CANCEL-BEFORE-RESPONSE")
async def test_cancel_during_request_records_unknown_but_local_denial_does_not(monkeypatch):
    entered = asyncio.Event()

    async def create(**kwargs):
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(
        "reachy_brain.providers.live.AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(
            responses=SimpleNamespace(create=create), close=AsyncMock()
        ),
    )
    settings = Settings(
        _env_file=None, openai_api_key="synthetic", iago_development_budget="unlimited"
    )
    gate = ProviderGate(settings)
    brain = AstraBrain(settings, gate)
    stream = brain.stream([], [])
    pending = asyncio.create_task(anext(stream))
    await asyncio.wait_for(entered.wait(), 1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert len(gate.usage) == 1 and gate.unknown_charges == 1
    row = gate.usage[0]
    assert row["usage"]["status"] == "interrupted"
    assert row["usage"]["request_id_origin"] == "local_attempt"
    assert row["request_id"].startswith("local-")
    gate.limit("openai")
    with pytest.raises(ProviderError, match="plan_limit"):
        await anext(brain.stream([], []))
    assert len(gate.usage) == 1
