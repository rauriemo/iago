"""Bounded nonblocking submission; a single worker serializes private transcript writes."""

import asyncio

import anyio

from reachy_brain.integrations.registry import ToolError

CURRENT_GENERATION = object()


class TranscriptRecorder:
    def __init__(self, store, *, capacity=128):
        self.store = store
        self.queue = asyncio.Queue(maxsize=capacity)
        self.state = {"enabled": False, "generation": 0}
        self.paused = False
        self.closed = False
        self.task = None
        self.control = asyncio.Lock()
        self.dropped = 0
        self.error = None
        self.gestures = {}

    async def start(self):
        self.state = await asyncio.to_thread(self.store.state)
        self.task = asyncio.create_task(self.run())

    def submit(
        self,
        session,
        entry,
        role=None,
        text=None,
        kind=None,
        *,
        metadata=None,
        remove=False,
        retire=False,
        generation=CURRENT_GENERATION,
    ):
        key = (session, entry)
        if retire:
            for existing, state in list(self.gestures.items()):
                if (
                    existing[0] == session
                    and (entry is None or existing == key)
                    and state == "live"
                ):
                    self.gestures.pop(existing)
            return True
        if remove and not self.closed:
            if self.gestures.get(key) != "live":
                return False
            drained = 0
            # Each admitted gesture reserves capacity for its eventual correction.
            # Corrections may displace unsaved ordinary text, never another correction.
            if self.queue.full():
                retained = []
                displaced = False
                while not self.queue.empty():
                    item = self.queue.get_nowait()
                    drained += 1
                    if not item[-1] and not displaced:
                        displaced = True
                    else:
                        retained.append(item)
                for item in retained:
                    self.queue.put_nowait(item)
                assert displaced, "Gesture correction reservation invariant"
                self.dropped += 1
                self.error = "transcript_queue_full"
            self.gestures[key] = "removing"
            self.queue.put_nowait((None, session, entry, None, None, None, None, True))
            # Replace accounting only after all retained work and the correction
            # are queued, so join() cannot observe a transient empty work count.
            for _ in range(drained):
                self.queue.task_done()
            return True
        if self.closed or self.paused or not self.state["enabled"]:
            return False
        if generation is CURRENT_GENERATION:
            generation = self.state["generation"]
        if generation is None or generation != self.state["generation"]:
            return False
        if (
            kind == "gesture"
            and key not in self.gestures
            and len(self.gestures) >= self.queue.maxsize
        ):
            self.dropped += 1
            self.error = "transcript_correction_capacity"
            return False
        try:
            self.queue.put_nowait((generation, session, entry, role, text, kind, metadata, remove))
            if kind == "gesture":
                self.gestures[key] = "live"
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            self.error = "transcript_queue_full"
            return False

    async def run(self):
        while True:
            generation, session, entry, role, text, kind, metadata, remove = await self.queue.get()
            try:
                if remove:
                    await asyncio.to_thread(
                        self.store.remove_entry, session, entry, generation=generation
                    )
                    self.gestures.pop((session, entry), None)
                else:
                    await asyncio.to_thread(
                        self.store.record,
                        session,
                        entry,
                        role,
                        text,
                        kind,
                        generation=generation,
                        metadata=metadata,
                    )
            except Exception as exc:
                self.dropped += 1
                self.error = exc.code if isinstance(exc, ToolError) else "transcript_storage_error"
            finally:
                self.queue.task_done()

    def snapshot(self):
        if self.closed or self.paused or not self.state["enabled"]:
            return None
        return self.state["generation"]

    async def change(self, action, value):
        async with self.control:
            self.paused = True
            try:
                operation = self.store.set_enabled if action == "enabled" else self.store.delete
                pending = asyncio.create_task(asyncio.to_thread(operation, value))
                try:
                    self.state = await asyncio.shield(pending)
                except asyncio.CancelledError:
                    # Thread work cannot be canceled. Keep the admission fence until
                    # the actual outcome is known, then propagate caller cancellation.
                    with anyio.CancelScope(shield=True):
                        while not pending.done():
                            try:
                                await asyncio.shield(pending)
                            except asyncio.CancelledError:
                                continue
                        self.state = pending.result()
                    raise
            finally:
                self.paused = False
        return self.status()

    def status(self):
        return {
            **self.state,
            "pending": self.queue.qsize(),
            "dropped": self.dropped,
            "error": self.error,
        }

    async def close(self):
        self.closed = True
        if not self.task:
            return
        try:
            async with asyncio.timeout(6):
                await self.queue.join()
        except TimeoutError:
            self.error = "transcript_flush_timeout"
        finally:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            while not self.queue.empty():
                self.queue.get_nowait()
                self.queue.task_done()
                self.dropped += 1
            self.gestures.clear()
