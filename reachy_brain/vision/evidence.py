"""Retain a bounded number of actual detector frames in the shared visual store."""

import asyncio
from dataclasses import replace


class EventEvidence:
    def __init__(self, store):
        self.store = store
        self.last = {}

    async def attach(self, events, jpeg, *, uncertainty=0):
        if not events or not jpeg:
            return events
        event = events[0]
        identity = (event.source, event.generation)
        # At most two supporting images per source/second. Co-occurring events
        # share one image; images and pins still use the existing global budgets.
        if event.captured - self.last.get(identity, -float("inf")) < 0.5:
            return events
        if len(jpeg) > 1024 * 1024:
            raise ValueError("support_image_limit")
        prepared = await asyncio.to_thread(self.store.prepare, jpeg)
        frame = self.store.add(
            event.source, event.generation, event.captured, prepared, support=True
        )
        frame.labels = ["detector supporting frame"] + sorted({e.kind for e in events})
        if uncertainty:
            frame.capture_time_known = False
            frame.timing_note = "Detector timing uncertainty: " + str(uncertainty) + " seconds"
        self.last[identity] = event.captured
        self.last = {
            key: value
            for key, value in self.last.items()
            if key[0] in self.store.sources and self.store.sources[key[0]].generation == key[1]
        }
        return [replace(e, frames=(frame.id,)) for e in events]
