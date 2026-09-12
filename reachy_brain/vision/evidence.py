"""Retain a bounded number of actual detector frames in the shared visual store."""

import asyncio
import json
from dataclasses import replace

from reachy_brain.integrations.registry import ToolError, bounded


class EventEvidence:
    def __init__(self, store):
        self.store = store
        self.last = {}

    async def attach(self, events, jpeg, *, uncertainty=0):
        if not events or not jpeg:
            return events
        event = events[0]
        identity = (event.source, event.generation)
        if len(events) > 32:
            raise ToolError("event_metadata_limit")
        if any(
            (e.source, e.generation, e.captured) != (event.source, event.generation, event.captured)
            for e in events
        ):
            raise ToolError("mixed_event_evidence")
        records = []
        for e in events:
            label = e.details.get("label", "")
            if any(
                not isinstance(value, str) or len(value) > limit
                for value, limit in ((e.id, 128), (e.kind, 80), (e.detector, 128), (label, 80))
            ):
                raise ToolError("invalid_event_metadata")
            records.append(
                {
                    "id": e.id,
                    "kind": e.kind,
                    "captured": e.captured,
                    "confidence": e.confidence,
                    "detector": e.detector,
                    "object_label": label,
                }
            )
        labels = ["detector supporting frame"] + sorted({e.kind for e in events})
        metadata = {"labels": labels, "events": records}
        bounded(metadata, 4096, 33)
        metadata_bytes = len(json.dumps(metadata, allow_nan=False).encode())
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
        frame.labels = labels
        frame.events = records
        frame.event_metadata_bytes = metadata_bytes
        self.store.expire()
        self.store.get(frame.id)
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
