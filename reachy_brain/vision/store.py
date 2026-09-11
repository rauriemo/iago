import base64
import io
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from PIL import Image, ImageOps

from reachy_brain.integrations.registry import ToolError


@dataclass
class Source:
    id: str
    owner: str
    kind: str
    label: str
    generation: int = 0
    enabled: bool = True
    last_capture: float = -1


@dataclass
class Frame:
    id: str
    source: str
    generation: int
    captured: float
    arrived: float
    width: int
    height: int
    image: bytes
    thumbnail: bytes
    labels: list[str] = field(default_factory=list)
    pin: str = ""
    capture_time_known: bool = True
    timing_note: str = ""

    @property
    def size(self):
        return len(self.image) + len(self.thumbnail)


class VisualStore:
    def __init__(
        self,
        *,
        max_bytes=512 * 1024 * 1024,
        retention=600,
        pin_bytes=64 * 1024 * 1024,
        pin_count=10,
        clock=time.time,
    ):
        self.max_bytes, self.retention = max_bytes, retention
        self.pin_bytes, self.pin_count, self.clock = pin_bytes, pin_count, clock
        self.sources: dict[str, Source] = {}
        self.frames: OrderedDict[str, Frame] = OrderedDict()
        self.context_generation = 0

    def source(self, owner, kind, label):
        if kind not in {"camera", "screen", "upload"} or len(self.sources) >= 8:
            raise ToolError("source_limit")
        item = Source(uuid.uuid4().hex, owner, kind, label[:120])
        self.sources[item.id] = item
        return item

    @staticmethod
    def prepare(data: bytes):
        if not 0 < len(data) <= 20 * 1024 * 1024:
            raise ToolError("image_byte_limit")
        try:
            with Image.open(io.BytesIO(data)) as raw:
                if raw.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ToolError("image_type")
                if raw.width * raw.height > 20_000_000 or max(raw.size) > 8192:
                    raise ToolError("image_dimension_limit")
                image = ImageOps.exif_transpose(raw).convert("RGB")
                output, thumbnail = io.BytesIO(), io.BytesIO()
                image.save(output, format="JPEG", quality=92)
                width, height = image.size
                image.thumbnail((320, 240))
                image.save(thumbnail, format="JPEG", quality=75)
                return width, height, output.getvalue(), thumbnail.getvalue()
        except (OSError, Image.DecompressionBombError) as exc:
            raise ToolError("invalid_image") from exc

    def add(self, source_id, generation, captured, prepared, *, support=False):
        source = self.sources.get(source_id)
        if not source or not source.enabled or source.generation != generation:
            raise ToolError("stale_source")
        now = self.clock()
        if (
            (not support and captured <= source.last_capture)
            or captured > now + 2
            or captured < now - self.retention
        ):
            raise ToolError("stale_frame")
        width, height, image, thumb = prepared
        if len(image) + len(thumb) > self.max_bytes:
            raise ToolError("image_byte_limit")
        frame = Frame(
            uuid.uuid4().hex, source_id, generation, captured, now, width, height, image, thumb
        )
        self.frames[frame.id] = frame
        source.last_capture = max(source.last_capture, captured)
        self.expire()
        return frame

    def totals(self):
        return {
            "rolling_bytes": sum(f.size for f in self.frames.values() if not f.pin),
            "pin_bytes": sum(f.size for f in self.frames.values() if f.pin),
            "pins": sum(bool(f.pin) for f in self.frames.values()),
            "frames": len(self.frames),
        }

    def expire(self):
        now = self.clock()
        total = sum(f.size for f in self.frames.values() if not f.pin)
        for key, frame in list(self.frames.items()):
            if not frame.pin and (frame.captured <= now - self.retention or total > self.max_bytes):
                total -= frame.size
                del self.frames[key]

    def get(self, frame_id):
        self.expire()
        frame = self.frames.get(frame_id)
        if frame is None:
            raise ToolError("expired")
        source = self.sources.get(frame.source)
        if not source or not source.enabled or source.generation != frame.generation:
            raise ToolError("stale_source")
        return frame

    def pin(self, frame_id, label):
        frame = self.get(frame_id)
        totals = self.totals()
        if not frame.pin and (
            totals["pins"] >= self.pin_count or totals["pin_bytes"] + frame.size > self.pin_bytes
        ):
            raise ToolError("pin_limit")
        frame.pin = label[:100] or "Reference"

    def unpin(self, frame_id):
        self.get(frame_id).pin = ""
        self.expire()

    def clear(self, source_id=None, *, disable=False, owner=None):
        self.context_generation += 1
        affected = []
        for source in self.sources.values():
            if (source_id is None or source.id == source_id) and (
                owner is None or owner == source.owner
            ):
                source.generation += 1
                source.last_capture = -1
                source.enabled = not disable
                affected.append(source.id)
        for key, frame in list(self.frames.items()):
            if frame.source in affected:
                del self.frames[key]
        if disable:
            for key in affected:
                del self.sources[key]
        return affected

    def describe(self, frame):
        source = self.sources[frame.source]
        return {
            "id": frame.id,
            "capture_time_known": frame.capture_time_known,
            "timing_note": frame.timing_note,
            "source": frame.source,
            "source_kind": source.kind,
            "source_label": source.label,
            "generation": frame.generation,
            "captured": frame.captured,
            "arrived": frame.arrived,
            "width": frame.width,
            "height": frame.height,
            "pin": frame.pin,
            "labels": frame.labels,
            "expires": None if frame.pin else frame.captured + self.retention,
        }

    def browse(self, *, source=None, start=0, end=float("inf"), cursor=0, limit=24, query=""):
        self.expire()
        candidates = [
            f
            for f in self.frames.values()
            if (not source or f.source == source)
            and start <= f.captured <= end
            and (not query or query.lower() in " ".join(f.labels + [f.pin]).lower())
        ]
        candidates.reverse()
        batch = candidates[max(0, cursor) : max(0, cursor) + min(24, max(1, limit))]
        return {
            "frames": [self.describe(f) for f in batch],
            "next_cursor": cursor + len(batch) if cursor + len(batch) < len(candidates) else None,
            "coverage": "Metadata/time/labels only; no semantic image index",
        }

    def image_input(self, frame_id, region=None, *, thumbnail=False):
        frame = self.get(frame_id)
        data = frame.thumbnail if thumbnail else frame.image
        if region is not None:
            x, y, w, h = region
            if min(x, y) < 0 or min(w, h) <= 0 or x + w > frame.width or y + h > frame.height:
                raise ToolError("invalid_region")
            with Image.open(io.BytesIO(frame.image)) as image:
                output = io.BytesIO()
                image.crop((x, y, x + w, y + h)).save(output, format="JPEG", quality=95)
                data = output.getvalue()
        return {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64," + base64.b64encode(data).decode(),
            "detail": "high",
        }
