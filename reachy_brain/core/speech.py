"""Bounded incremental speech with sink credits independent of heard-answer acknowledgments."""

import asyncio
import base64
import contextlib
import re


class SpeechStream:
    def __init__(
        self,
        epoch,
        valid,
        emit,
        voices,
        provider,
        ledger,
        stop_generation,
        *,
        max_buffer_bytes=96000,
        allow_fallback=True,
        on_error=None,
    ):
        self.epoch, self.valid, self.emit = epoch, valid, emit
        self.voices, self.provider, self.ledger = voices, provider, ledger
        self.stop_generation = stop_generation
        self.maximum = max_buffer_bytes
        self.allow_fallback = allow_fallback
        self.on_error = on_error
        self.failed = False
        self.queue = asyncio.Queue(maxsize=2)
        self.credit = asyncio.Event()
        self.pending = {}
        self.pending_bytes = 0
        self.sequence = 0
        self.segment = 0
        self.buffer = ""
        self.total = 0
        self.closed = False
        self.emitted = False
        self.worker = None

    async def __aenter__(self):
        self.worker = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                if self.buffer.strip():
                    await self._put(self.buffer.strip())
                self.buffer = ""
                await self._put(None)
                await self.worker
                if not self.failed:
                    await self.emit("speech_queued")
        finally:
            self.closed = True
            self.worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.worker

    async def _put(self, segment):
        if self.failed:
            return
        if self.worker.done():
            await self.worker
            raise RuntimeError("speech_closed")
        put = asyncio.create_task(self.queue.put(segment))
        try:
            await asyncio.wait({put, self.worker}, return_when=asyncio.FIRST_COMPLETED)
            if self.worker.done():
                await self.worker
                if self.failed:
                    return
            await put
        finally:
            if not put.done():
                put.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await put

    async def feed(self, text):
        self.total += len(text)
        if self.total > 12000:
            raise ValueError("answer_text_limit")
        if self.failed:
            return
        self.buffer += text
        while self.buffer:
            boundary = re.search(r"[.!?](?:\s+|$)|\n", self.buffer)
            cut = boundary.end() if boundary else 0
            if not cut and len(self.buffer) >= 400:
                cut = self.buffer.rfind(" ", 0, 400)
                if cut < 1:
                    cut = 400
            if not cut:
                break
            segment, self.buffer = self.buffer[:cut].strip(), self.buffer[cut:]
            if segment:
                await self._put(segment)

    async def question(self, text, question_id):
        if self.buffer.strip():
            await self._put(self.buffer.strip())
        self.buffer = ""
        self.total += len(text)
        if self.total > 12000:
            raise ValueError("answer_text_limit")
        await self._put({"text": text, "question": question_id})

    def acknowledge(self, epoch, sequence):
        if epoch != self.epoch or sequence not in self.pending:
            return
        for key in list(self.pending):
            if key <= sequence:
                self.pending_bytes -= self.pending.pop(key)
        self.credit.set()

    async def _pcm(self, pcm, segment):
        if len(pcm) % 2:
            raise ValueError("invalid_pcm")
        for offset in range(0, len(pcm), 1920):
            chunk = pcm[offset : offset + 1920]
            while self.pending_bytes + len(chunk) > self.maximum:
                self.credit.clear()
                async with asyncio.timeout(10):
                    await self.credit.wait()
            if not self.valid():
                raise asyncio.CancelledError
            sequence = self.sequence
            self.sequence += 1
            self.pending[sequence] = len(chunk)
            self.pending_bytes += len(chunk)
            self.emitted = True
            await self.emit(
                "audio",
                segment=segment,
                sequence=sequence,
                pcm=base64.b64encode(chunk).decode(),
                rate=24000,
            )

    async def _run(self):
        try:
            await self._run_speech()
        except Exception as exc:
            if self.on_error is None:
                raise
            self.failed = True
            self.buffer = ""
            while not self.queue.empty():
                self.queue.get_nowait()
            await self.on_error(exc)

    async def _run_speech(self):
        authorized = False
        while True:
            text = await self.queue.get()
            if text is None:
                return
            question = text.get("question") if isinstance(text, dict) else None
            text = text["text"] if isinstance(text, dict) else text
            if not self.valid():
                raise asyncio.CancelledError
            if not authorized:
                await self.emit("authorize", acknowledged_stop=self.stop_generation)
                authorized = True
            segment = f"{self.epoch}-{self.segment}"
            self.segment += 1
            self.ledger.add(self.epoch, segment, text)
            if question:
                await self.emit("question_segment", question=question, segment=segment, text=text)
            try:
                async with contextlib.aclosing(self.voices[self.provider].stream(text)) as source:
                    async for pcm in source:
                        await self._pcm(pcm, segment)
            except Exception:
                # Once bytes have left the host, replay risks duplicating heard speech.
                if not self.allow_fallback or self.provider != "elevenlabs" or self.emitted:
                    raise
                self.provider = "openai"
                await self.emit("voice_fallback", reason="primary_failure_before_audio")
                async with contextlib.aclosing(self.voices[self.provider].stream(text)) as source:
                    async for pcm in source:
                        await self._pcm(pcm, segment)
            await self.emit("segment_end", segment=segment)
