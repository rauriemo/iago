"""Bounded image-worker ownership on the application event loop."""

import asyncio
from contextlib import contextmanager

from reachy_brain.integrations.registry import ToolError


class FrameIngress:
    def __init__(self):
        self.active = {}

    @contextmanager
    def slot(self, source):
        if source in self.active or len(self.active) >= 2:
            raise ToolError("image_ingress_busy")
        self.active[source] = None
        worker = None

        async def prepare(data, handler):
            nonlocal worker
            if worker is not None:
                raise RuntimeError("decode_already_started")
            worker = asyncio.create_task(asyncio.to_thread(handler, data))
            self.active[source] = worker

            def completed(task):
                if self.active.get(source) is task:
                    self.active.pop(source)
                if not task.cancelled():
                    task.exception()  # Retrieve errors even when the HTTP caller has left.

            worker.add_done_callback(completed)
            return await asyncio.shield(worker)

        try:
            yield prepare
        finally:
            if worker is None:
                self.active.pop(source, None)
            # Once submitted, only the actual worker completion releases its slot.
