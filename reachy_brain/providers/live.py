"""Real provider adapters. Exceptions exposed to the UI contain no credential/body data."""

import asyncio
import base64
import contextlib
import hashlib
import json
import math
import re
import time
import uuid
from collections.abc import AsyncIterator
from functools import wraps
from urllib.parse import quote

import httpx
from openai import APIStatusError, AsyncOpenAI, RateLimitError
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from reachy_brain.config import RUNTIME_INSTRUCTIONS
from reachy_brain.integrations.registry import ToolError
from reachy_brain.providers.circuit import CircuitOpen, SpeechCircuit
from reachy_brain.providers.costs import RateTable
from reachy_brain.providers.recognition_usage import RecognitionUsage


class ProviderError(Exception):
    def __init__(self, provider: str, code: str):
        self.provider, self.code = provider, code
        super().__init__(f"{provider}: {code}")


def speech_circuit(provider):
    def decorate(method):
        @wraps(method)
        async def stream(self, text):
            self.gate.require(provider)
            try:
                with self.circuit.attempt():
                    async with contextlib.aclosing(method(self, text)) as source:
                        async for chunk in source:
                            yield chunk
            except CircuitOpen:
                raise ProviderError(provider, "speech_circuit_open") from None

        return stream

    return decorate


class ProviderGate:
    """Stop further billable work after provider plan/quota limits until explicit reset."""

    def __init__(self, settings):
        self.settings = settings
        self.limited: set[str] = set()
        self.usage: list[dict] = []
        self.estimated_usd = 0.0
        self.rates = RateTable.load(settings.cost_rate_table)
        self.unknown_charges = 0
        self.seen_usage = set()
        self.persistence = None
        self.active = {}

    def require(self, provider):
        budget = self.settings.iago_development_budget
        if self.persistence and (self.persistence.error or self.persistence.closing):
            raise ProviderError(provider, "usage_persistence_unavailable")
        if len(self.seen_usage) >= 10000:
            raise ProviderError(provider, "usage_capacity")
        if provider in self.limited:
            raise ProviderError(provider, "plan_limit")
        if budget != "unlimited" and (budget <= 0 or self.estimated_usd >= budget):
            raise ProviderError(provider, "development_budget")

    async def checkpoint(self, provider):
        if self.persistence:
            try:
                async with asyncio.timeout(10):
                    await self.persistence.flush()
            except (RuntimeError, TimeoutError):
                raise ProviderError(provider, "usage_persistence_unavailable") from None
        self.require(provider)

    def begin_active(self, provider, model, counters):
        if len(self.active) >= 32:
            raise ProviderError(provider, "active_usage_capacity")
        identity = "local-" + uuid.uuid4().hex
        self.active[identity] = {
            "provider": provider,
            "model": model,
            "started": time.monotonic(),
            "counters": counters,
        }
        if self.persistence:
            self.persistence.changed()
        return identity

    def end_active(self, identity):
        if self.active.pop(identity, None) is not None and self.persistence:
            self.persistence.changed()

    def active_usage(self):
        # Only numeric operational counters cross the diagnostic boundary. Never
        # copy speech text, request bodies or arbitrary provider error strings.
        return [
            {
                "attempt_id": identity,
                "provider": row["provider"],
                "model": row["model"],
                "status": "active",
                "elapsed_seconds": max(0, time.monotonic() - row["started"]),
                "counters": {
                    key: value
                    for key, value in row["counters"].items()
                    if type(value) in (int, float) and math.isfinite(value)
                },
                "billing_complete": False,
            }
            for identity, row in self.active.items()
        ]

    def limit(self, provider):
        if provider in self.limited:
            return
        self.limited.add(provider)
        if self.persistence:
            self.persistence.changed()

    def record(self, provider, request_id, usage, *, model=None, service_tier="default"):
        identity = (provider, request_id)
        if request_id and identity in self.seen_usage:
            return
        if request_id and len(self.seen_usage) < 10000:
            self.seen_usage.add(identity)
        undispatched = usage.get("dispatched") is False
        estimate = 0 if undispatched else self.rates.estimate(model, usage, service_tier=service_tier)
        fully_priced = undispatched or (
            estimate is not None and usage.get("billing_usage_complete") is not False
        )
        if not fully_priced:
            self.unknown_charges += 1
        if estimate is not None:
            self.estimated_usd += float(estimate)
        self.usage.append(
            {
                "provider": provider,
                "request_id": request_id,
                "usage": usage,
                "model": model,
                "estimated_usd": float(estimate) if estimate is not None else None,
                "usage_priced_completely": fully_priced,
                "rate_date": str(self.rates.date),
                "service_tier": service_tier,
            }
        )
        del self.usage[:-500]
        if self.persistence:
            self.persistence.changed()

    @contextlib.contextmanager
    def speech_attempt(self, provider, model, characters, *, dispatched=True):
        attempt = {
            "input_characters": characters,
            "received_pcm_bytes": 0,
            "request_id": "",
            "status": "completed",
            "dispatched": dispatched,
        }
        started = time.monotonic()
        active_id = self.begin_active(provider, model, attempt)
        try:
            yield attempt
        except BaseException as exc:
            attempt["status"] = (
                "interrupted"
                if isinstance(exc, (asyncio.CancelledError, GeneratorExit))
                else "failed"
            )
            raise
        finally:
            self.end_active(active_id)
            request_id = attempt.pop("request_id")
            attempt["request_id_origin"] = "provider" if request_id else "local_attempt"
            attempt["received_audio_seconds"] = attempt["received_pcm_bytes"] / 48000
            attempt["elapsed_seconds"] = time.monotonic() - started
            attempt["billing_usage_known"] = not attempt["dispatched"]
            attempt["attempt_id"] = active_id
            self.record(provider, request_id or active_id, attempt, model=model)


class AstraBrain:
    def __init__(self, settings, gate):
        self.settings, self.gate = settings, gate
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value() or "not-configured",
            max_retries=0,
            timeout=30,
        )

    async def stream(self, messages: list, tools: list, *, max_tokens=1000) -> AsyncIterator[dict]:
        self.gate.require("openai")
        if not self.settings.openai_api_key.get_secret_value():
            raise ProviderError("openai", "missing_key")
        started = time.monotonic()
        request_id = ""
        provenance = {}
        service_tier = "default"
        usage = {}
        status = "failed"
        recorded = False
        dispatched = False
        active_counters = {"received_text_characters": 0}
        active_id = self.gate.begin_active("astra", self.settings.brain_model, active_counters)

        def record_attempt():
            nonlocal recorded
            if recorded:
                return
            self.gate.end_active(active_id)
            self.gate.record(
                "astra",
                request_id or active_id,
                {
                    **usage,
                    "status": status,
                    "elapsed_seconds": time.monotonic() - started,
                    "request_id_origin": "provider" if request_id else "local_attempt",
                    "usage_returned": bool(usage),
                    "attempt_id": active_id,
                    "dispatched": dispatched,
                },
                model=self.settings.brain_model,
                service_tier=service_tier,
            )
            recorded = True

        try:
            await self.gate.checkpoint("openai")
            dispatched = True
            stream = await self.client.responses.create(
                model=self.settings.brain_model,
                reasoning={"effort": self.settings.brain_reasoning_effort},
                instructions=RUNTIME_INSTRUCTIONS
                + "\nConversation style and language preferences:\n"
                + self.settings.personality,
                input=messages,
                tools=tools,
                store=False,
                service_tier="default",
                stream=True,
                max_output_tokens=max_tokens,
            )
            async with stream:
                async for event in stream:
                    if event.type in {
                        "response.created",
                        "response.completed",
                        "response.failed",
                        "response.incomplete",
                    }:
                        request_id = event.response.id
                        provenance = {
                            "response_id": request_id,
                            "requested_model": self.settings.brain_model,
                        }
                        reported = getattr(event.response, "model", None)
                        if isinstance(reported, str) and 0 < len(reported) <= 128:
                            provenance["reported_model"] = reported
                        service_tier = getattr(event.response, "service_tier", None) or "default"
                        if event.response.usage:
                            usage = event.response.usage.model_dump()
                    if event.type == "response.output_text.delta":
                        active_counters["received_text_characters"] += len(event.delta)
                        yield {"type": "text", "text": event.delta, "provenance": dict(provenance)}
                    elif event.type == "response.output_item.done":
                        yield {
                            "type": "item",
                            "item": event.item.model_dump(mode="json", exclude_none=True),
                            "provenance": dict(provenance),
                        }
                    elif event.type == "response.completed":
                        status = "completed"
                        record_attempt()
                        yield {"type": "done", "usage": usage, "provenance": dict(provenance)}
                        return
                    elif event.type in {"response.failed", "response.incomplete", "error"}:
                        status = "incomplete" if event.type == "response.incomplete" else "failed"
                        raise ProviderError("openai", event.type)
                status = "incomplete"
                raise ProviderError("openai", "incomplete_stream")
        except (asyncio.CancelledError, GeneratorExit):
            status = "interrupted"
            raise
        except RateLimitError:
            self.gate.limit("openai")
            raise ProviderError("openai", "plan_limit") from None
        except APIStatusError as exc:
            raise ProviderError("openai", f"http_{exc.status_code}") from None
        finally:
            record_attempt()

    async def close(self):
        await self.client.close()


class OpenAISpeech:
    def __init__(self, settings, gate):
        self.settings, self.gate = settings, gate
        self.circuit = SpeechCircuit()
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value() or "not-configured",
            max_retries=0,
            timeout=20,
        )

    @speech_circuit("openai")
    async def stream(self, text: str) -> AsyncIterator[bytes]:
        self.gate.require("openai")
        with self.gate.speech_attempt(
            "openai_tts", self.settings.openai_tts_model, len(text), dispatched=False
        ) as attempt:
            try:
                await self.gate.checkpoint("openai")
                attempt["dispatched"] = True
                async with self.client.audio.speech.with_streaming_response.create(
                    model=self.settings.openai_tts_model,
                    voice=self.settings.openai_tts_voice,
                    input=text,
                    response_format="pcm",
                ) as response:
                    attempt["request_id"] = response.headers.get("x-request-id", "")
                    async for chunk in response.iter_bytes(chunk_size=1920):
                        attempt["received_pcm_bytes"] += len(chunk)
                        yield chunk
            except RateLimitError:
                self.gate.limit("openai")
                raise ProviderError("openai", "plan_limit") from None
            except APIStatusError as exc:
                raise ProviderError("openai", f"http_{exc.status_code}") from None

    async def close(self):
        await self.client.close()


class ElevenSpeech:
    def __init__(self, settings, gate):
        self.settings, self.gate = settings, gate
        self.circuit = SpeechCircuit()
        self.validation_lock = asyncio.Lock()
        self.validated = None

    async def validate(self) -> dict:
        async with self.validation_lock:
            identity = hashlib.sha256(
                json.dumps(
                    [
                        self.settings.elevenlabs_api_key.get_secret_value(),
                        self.settings.elevenlabs_voice_id,
                        self.settings.elevenlabs_model_id,
                        "pcm_24000",
                    ]
                ).encode()
            ).hexdigest()
            try:
                self.gate.require("elevenlabs")
                self.circuit.check()
                if (
                    self.validated
                    and self.validated[0] == identity
                    and time.monotonic() < self.validated[1]
                ):
                    return {
                        "valid": True,
                        "reason": "voice_model_pcm_stream_verified",
                        "cached": True,
                    }
                self.validated = None
                async with asyncio.timeout(25):
                    metadata = await self.validate_metadata()
                    if not metadata["valid"]:
                        return metadata
                    total = 0
                    async with contextlib.aclosing(self.stream("Iago voice check.")) as stream:
                        async for pcm in stream:
                            total += len(pcm)
                            if len(pcm) % 2 or total > 240000:
                                raise ProviderError("elevenlabs", "invalid_pcm_probe")
                    if total < 4800:
                        raise ProviderError("elevenlabs", "empty_or_short_pcm_probe")
                self.validated = (identity, time.monotonic() + 300)
                return {"valid": True, "reason": "voice_model_pcm_stream_verified", "cached": False}
            except ProviderError as exc:
                return {"valid": False, "reason": exc.code}
            except CircuitOpen:
                return {"valid": False, "reason": "speech_circuit_open"}
            except (TimeoutError, httpx.HTTPError, WebSocketException, ValueError, OSError):
                return {"valid": False, "reason": "voice_validation_unavailable"}

    async def validate_metadata(self) -> dict:
        key = self.settings.elevenlabs_api_key.get_secret_value()
        voice = self.settings.elevenlabs_voice_id
        if not key or not voice:
            return {"valid": False, "reason": "missing_key_or_voice"}
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,64}", voice):
            return {"valid": False, "reason": "invalid_voice_id"}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"https://api.elevenlabs.io/v1/voices/{quote(voice, safe='')}",
                headers={"xi-api-key": key},
            )
            if response.status_code != 200:
                return {"valid": False, "reason": f"voice_http_{response.status_code}"}
            models = await client.get(
                "https://api.elevenlabs.io/v1/models", headers={"xi-api-key": key}
            )
            if models.status_code != 200 or not any(
                m["model_id"] == self.settings.elevenlabs_model_id
                and m.get("can_do_text_to_speech")
                for m in models.json()
            ):
                return {"valid": False, "reason": "unsupported_model"}
        return {
            "valid": True,
            "reason": "voice_and_model_access_verified_format_requires_stream_test",
        }

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        try:
            async with contextlib.aclosing(self._stream_pcm(text)) as stream:
                async for pcm in stream:
                    yield pcm
        except Exception:
            self.validated = None
            raise

    @speech_circuit("elevenlabs")
    async def _stream_pcm(self, text: str) -> AsyncIterator[bytes]:
        self.gate.require("elevenlabs")
        voice = quote(self.settings.elevenlabs_voice_id, safe="")
        model = quote(self.settings.elevenlabs_model_id, safe="")
        uri = f"wss://api.elevenlabs.io/v1/text-to-speech/{voice}/stream-input?model_id={model}&output_format=pcm_24000"
        with self.gate.speech_attempt(
            "elevenlabs_tts", self.settings.elevenlabs_model_id, len(text), dispatched=False
        ) as attempt:
            await self.gate.checkpoint("elevenlabs")
            attempt["dispatched"] = True
            async with asyncio.timeout(20):
                async with connect(uri, max_size=2**20, max_queue=4, open_timeout=10) as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "text": " ",
                                "xi_api_key": self.settings.elevenlabs_api_key.get_secret_value(),
                                "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
                            }
                        )
                    )
                    await ws.send(json.dumps({"text": text + " ", "flush": True}))
                    await ws.send(json.dumps({"text": ""}))
                    async for raw in ws:
                        message = json.loads(raw)
                        if message.get("error") or message.get("code"):
                            code = str(message.get("code", message.get("error", "stream_error")))
                            if any(x in code.lower() for x in ["quota", "limit", "429", "credit"]):
                                self.gate.limit("elevenlabs")
                                raise ProviderError("elevenlabs", "plan_limit")
                            raise ProviderError("elevenlabs", "stream_error")
                        if message.get("audio"):
                            pcm = base64.b64decode(message["audio"], validate=True)
                            attempt["received_pcm_bytes"] += len(pcm)
                            yield pcm
                        if message.get("isFinal") is True:
                            break
                    else:
                        # A clean transport close does not establish synthesis completion.
                        raise ProviderError("elevenlabs", "incomplete_stream")


class Transcription:
    """Dedicated transcription websocket; local device owns VAD/commit timing."""

    def __init__(self, settings, gate):
        self.settings, self.gate = settings, gate
        self.ws = None
        self.manager = None
        self.input_lock = asyncio.Lock()
        self.buffered_bytes = 0
        self.input_failed = False
        self.accounting = None

    async def start(self):
        if self.accounting is not None:
            raise ProviderError("openai", "transcription_already_started")
        self.gate.require("openai")
        self.accounting = RecognitionUsage(self.gate, self.settings.stt_model, dispatched=False)
        try:
            await self.gate.checkpoint("openai")
            self.accounting.dispatched = True
            await self._start_transport()
        except BaseException as exc:
            self.accounting.status = (
                "interrupted" if isinstance(exc, asyncio.CancelledError) else "failed"
            )
            with contextlib.suppress(Exception):
                await self.close()
            raise

    async def _start_transport(self):
        self.manager = connect(
            "wss://api.openai.com/v1/realtime?intent=transcription",
            additional_headers={
                "Authorization": "Bearer " + self.settings.openai_api_key.get_secret_value()
            },
            max_size=2**20,
            max_queue=8,
            open_timeout=15,
        )
        self.ws = await self.manager.__aenter__()
        await self.ws.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "type": "transcription",
                        "audio": {
                            "input": {
                                "format": {"type": "audio/pcm", "rate": 24000},
                                "transcription": {"model": self.settings.stt_model},
                                "turn_detection": None,
                            }
                        },
                    },
                }
            )
        )
        async with asyncio.timeout(15):
            while True:
                event = json.loads(await self.ws.recv())
                if event["type"] in {"session.created", "session.updated"}:
                    self.accounting.session(event)
                if event["type"] == "session.updated":
                    return
                if event["type"] == "error":
                    raise ProviderError("openai", "transcription_setup_error")

    async def _send_input(self, message):
        if self.input_failed or not self.ws:
            raise ProviderError("openai", "transcription_disconnected")
        try:
            if self.gate:
                self.gate.require("openai")
            await self.ws.send(json.dumps(message))
        except BaseException as exc:
            # A canceled send may already have reached the provider. Retire this
            # transport rather than replaying an uncertain append or commit.
            self.input_failed = True
            if self.accounting:
                self.accounting.status = (
                    "interrupted" if isinstance(exc, asyncio.CancelledError) else "failed"
                )
            with contextlib.suppress(Exception):
                await self.close()
            raise

    async def append(self, pcm: bytes):
        if len(pcm) > 12000 or len(pcm) % 2:
            raise ToolError("audio_packet_limit")
        if not pcm:
            return
        async with self.input_lock:
            if self.accounting:
                self.accounting.add("attempted_pcm_bytes", len(pcm))
            await self._send_input(
                {"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode()}
            )
            self.buffered_bytes += len(pcm)
            if self.accounting:
                self.accounting.add("sent_pcm_bytes", len(pcm))

    async def commit(self):
        async with self.input_lock:
            if not self.buffered_bytes:
                return False
            if self.accounting:
                self.accounting.add("attempted_commits")
            await self._send_input({"type": "input_audio_buffer.commit"})
            self.buffered_bytes = 0
            if self.accounting:
                self.accounting.add("sent_commits")
            return True

    async def events(self):
        try:
            async for raw in self.ws:
                event = json.loads(raw)
                if event["type"] == "error":
                    code = event.get("error", {}).get("code", "")
                    if "quota" in code or "rate_limit" in code:
                        self.gate.limit("openai")
                    raise ProviderError("openai", "transcription_error")
                if (
                    self.accounting
                    and event["type"] == "conversation.item.input_audio_transcription.completed"
                ):
                    self.accounting.completed(event)
                yield event
            if self.accounting and not self.accounting.finalized:
                self.accounting.status = "disconnected"
        except BaseException as exc:
            if self.accounting and not self.accounting.finalized:
                self.accounting.status = (
                    "interrupted"
                    if isinstance(exc, (asyncio.CancelledError, GeneratorExit))
                    else "failed"
                )
            raise

    async def close(self):
        ws, self.ws = self.ws, None
        if self.accounting:
            self.accounting.finish()
        if ws:
            await ws.close()
