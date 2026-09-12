import base64
import hashlib
import io
import json
import math
import re
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field

import numpy as np
from PIL import Image, ImageOps

from reachy_brain.integrations.registry import ToolError
from reachy_brain.vision.ingress import FrameIngress


@dataclass
class Source:
    id: str
    owner: str
    kind: str
    label: str
    generation: int = 0
    enabled: bool = True
    last_capture: float = -1
    last_frame_sequence: int = -1
    last_detector_sequence: int = -1
    upload_attempts: int = 0
    upload_accepted: int = 0
    upload_rejected: int = 0


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
    image_sha256: str
    sharpness: float
    labels: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    event_metadata_bytes: int = 0
    speech_terms: list[str] = field(default_factory=list)
    speech_metadata_bytes: int = 0
    speech_index_limited: bool = False
    pin: str = ""
    capture_time_known: bool = True
    source_frame_id: str = ""
    capture_uncertainty: float | None = None
    source_monotonic: float | None = None
    clock_uncertainty: float | None = None
    clock_owner: str = ""
    timing_note: str = ""
    transformation: dict = field(default_factory=dict)
    transformation_bytes: int = 0

    @property
    def size(self):
        return (
            len(self.image)
            + len(self.thumbnail)
            + self.event_metadata_bytes
            + self.speech_metadata_bytes
            + len(self.pin.encode("utf-8"))
            + self.transformation_bytes
        )


class VisualStore:
    def __init__(
        self,
        *,
        max_bytes=512 * 1024 * 1024,
        retention=600,
        pin_bytes=64 * 1024 * 1024,
        pin_count=10,
        max_frames=6000,
        clock=time.time,
    ):
        if type(max_frames) is not int or not 1 <= max_frames <= 6000:
            raise ValueError("invalid_frame_count_limit")
        self.max_frames = max_frames
        self.max_bytes, self.retention = max_bytes, retention
        self.pin_bytes, self.pin_count, self.clock = pin_bytes, pin_count, clock
        self.sources: dict[str, Source] = {}
        self.capture_hooks = {}
        self.frames: OrderedDict[str, Frame] = OrderedDict()
        self.context_generation = 0
        self.image_work = FrameIngress()

    def source(self, owner, kind, label):
        if kind not in {"camera", "screen", "upload"} or len(self.sources) >= 8:
            raise ToolError("source_limit")
        item = Source(uuid.uuid4().hex, owner, kind, label[:120])
        self.sources[item.id] = item
        return item

    @staticmethod
    def prepare(data: bytes, *, preserve_png=False):
        if not 0 < len(data) <= 20 * 1024 * 1024:
            raise ToolError("image_byte_limit")
        try:
            with Image.open(io.BytesIO(data)) as raw:
                if raw.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ToolError("image_type")
                if raw.width * raw.height > 20_000_000 or max(raw.size) > 8192:
                    raise ToolError("image_dimension_limit")
                orientation = raw.getexif().get(274, 1)
                if type(orientation) is not int or orientation not in range(1, 9):
                    raise ToolError("invalid_image_orientation")
                stored_format = "PNG" if preserve_png and raw.format == "PNG" else "JPEG"
                color_mode = (
                    "RGBA"
                    if stored_format == "PNG"
                    and ("A" in raw.getbands() or "transparency" in raw.info)
                    else "RGB"
                )
                transformation = {
                    "input_width": raw.width,
                    "input_height": raw.height,
                    "input_format": raw.format,
                    "exif_orientation": orientation,
                    "orientation_applied": orientation != 1,
                    "stored_format": stored_format,
                    "stored_color_mode": color_mode,
                    "crop_coordinate_space": "stored_oriented_pixels",
                }
                image = ImageOps.exif_transpose(raw).convert(color_mode)
                image.info.clear()
                output, thumbnail = io.BytesIO(), io.BytesIO()
                image.save(
                    output,
                    format=stored_format,
                    **({"quality": 92} if stored_format == "JPEG" else {}),
                )
                width, height = image.size
                image.thumbnail((320, 240))
                pixels = np.asarray(image.convert("L"), dtype=np.float32)
                # Bounded thumbnail gradient energy: a ranking aid, not legibility.
                sharpness = sum(
                    float(np.mean(np.diff(pixels, axis=axis) ** 2))
                    for axis in (0, 1)
                    if pixels.shape[axis] > 1
                )
                image.convert("RGB").save(thumbnail, format="JPEG", quality=75)
                stored = output.getvalue()
                return (
                    width,
                    height,
                    stored,
                    thumbnail.getvalue(),
                    hashlib.sha256(stored).hexdigest(),
                    sharpness,
                    transformation,
                )
        except (OSError, Image.DecompressionBombError) as exc:
            raise ToolError("invalid_image") from exc

    def validate_frame(self, source_id, generation, captured, *, support=False, sequence=None):
        source = self.sources.get(source_id)
        if not source or not source.enabled or source.generation != generation:
            raise ToolError("stale_source")
        if sequence is not None and (
            type(sequence) is not int
            or not 0 <= sequence <= 9007199254740991
            or sequence <= source.last_frame_sequence
        ):
            raise ToolError("stale_frame_sequence")
        now = self.clock()
        if (
            type(captured) not in (int, float)
            or not math.isfinite(captured)
            or (
                not support
                and (
                    captured < source.last_capture
                    or (captured == source.last_capture and sequence is None)
                )
            )
            or captured > now + 2
            or captured < now - self.retention
        ):
            raise ToolError("stale_frame")
        return source

    def add(
        self,
        source_id,
        generation,
        captured,
        prepared,
        *,
        support=False,
        capture_uncertainty=None,
        sequence=None,
    ):
        if capture_uncertainty is not None and (
            type(capture_uncertainty) not in (int, float)
            or not math.isfinite(capture_uncertainty)
            or not 0 <= capture_uncertainty <= self.retention
        ):
            raise ToolError("invalid_capture_uncertainty")
        source = self.validate_frame(
            source_id, generation, captured, support=support, sequence=sequence
        )
        now = self.clock()
        width, height, image, thumb, image_sha256, sharpness, transformation = prepared
        transformation_bytes = len(
            json.dumps(transformation, separators=(",", ":")).encode("utf-8")
        )
        if len(image) + len(thumb) + transformation_bytes > self.max_bytes:
            raise ToolError("image_byte_limit")
        frame = Frame(
            uuid.uuid4().hex,
            source_id,
            generation,
            captured,
            now,
            width,
            height,
            image,
            thumb,
            image_sha256,
            sharpness,
            source_frame_id=str(sequence) if sequence is not None else "",
            capture_uncertainty=capture_uncertainty,
            transformation=dict(transformation),
            transformation_bytes=transformation_bytes,
        )
        self.frames[frame.id] = frame
        if sequence is not None:
            source.last_frame_sequence = sequence
        source.last_capture = max(source.last_capture, captured)
        self.expire()
        return frame

    @contextmanager
    def upload_attempt(self, source_id, generation):
        source = self.sources.get(source_id)

        def valid():
            return (
                source is not None
                and self.sources.get(source_id) is source
                and source.enabled
                and source.generation == generation
            )

        if valid():
            source.upload_attempts += 1
        try:
            yield
        except BaseException:
            if valid():
                source.upload_rejected += 1
            raise
        else:
            if valid():
                source.upload_accepted += 1

    def source_status(self):
        self.expire()
        rows = []
        for source in self.sources.values():
            frames = [frame for frame in self.frames.values() if frame.source == source.id]
            rolling = [frame for frame in frames if not frame.pin]
            rows.append(
                {
                    **asdict(source),
                    "retained": {
                        "rolling_frames": len(rolling),
                        "rolling_bytes": sum(frame.size for frame in rolling),
                        "pin_frames": sum(bool(frame.pin) for frame in frames),
                        "pin_bytes": sum(frame.size for frame in frames if frame.pin),
                        "earliest_capture": min(
                            (frame.captured for frame in rolling), default=None
                        ),
                        "latest_capture": max((frame.captured for frame in rolling), default=None),
                    },
                }
            )
        return rows

    def totals(self):
        self.expire()
        rolling = [f for f in self.frames.values() if not f.pin]
        return {
            "rolling_bytes": sum(f.size for f in rolling),
            "pin_bytes": sum(f.size for f in self.frames.values() if f.pin),
            "pins": sum(bool(f.pin) for f in self.frames.values()),
            "frames": len(self.frames),
            "rolling_frames": len(rolling),
            "rolling_frame_limit": self.max_frames,
            "model_image_workers": len(self.image_work.active),
            "model_image_worker_limit": 2,
            "earliest_rolling_capture": min((f.captured for f in rolling), default=None),
            "latest_rolling_capture": max((f.captured for f in rolling), default=None),
        }

    def expire(self):
        now = self.clock()
        total = sum(f.size for f in self.frames.values() if not f.pin)
        count = sum(not f.pin for f in self.frames.values())
        for key, frame in sorted(self.frames.items(), key=lambda item: item[1].captured):
            if not frame.pin and (
                frame.captured <= now - self.retention
                or total > self.max_bytes
                or count > self.max_frames
            ):
                total -= frame.size
                count -= 1
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
        if not isinstance(label, str):
            raise ToolError("invalid_pin_label")
        label = label[:100] or "Reference"
        frame = self.get(frame_id)
        totals = self.totals()
        label_bytes = len(label.encode("utf-8"))
        added = (
            label_bytes - len(frame.pin.encode("utf-8")) if frame.pin else frame.size + label_bytes
        )
        if (not frame.pin and totals["pins"] >= self.pin_count) or totals[
            "pin_bytes"
        ] + added > self.pin_bytes:
            raise ToolError("pin_limit")
        frame.pin = label

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
                source.last_frame_sequence = -1
                source.last_detector_sequence = -1
                source.upload_attempts = source.upload_accepted = source.upload_rejected = 0
                source.enabled = not disable
                affected.append(source.id)
        for key, frame in list(self.frames.items()):
            if frame.source in affected:
                del self.frames[key]
        if disable:
            for key in affected:
                self.capture_hooks.pop(key, None)
                del self.sources[key]
        return affected

    @staticmethod
    def time_radius(frame):
        return (
            frame.capture_uncertainty
            if (frame.capture_time_known and frame.capture_uncertainty is not None)
            else 0
        )

    def overlaps(self, frame, start, end):
        radius = self.time_radius(frame)
        return frame.captured + radius >= start and frame.captured - radius <= end

    def describe(self, frame):
        source = self.sources[frame.source]
        return {
            "id": frame.id,
            "image_sha256": frame.image_sha256,
            "sharpness": frame.sharpness,
            "sharpness_method": "thumbnail_grayscale_gradient_energy_v1",
            "speech_index_limited": frame.speech_index_limited,
            "capture_time_known": frame.capture_time_known,
            "capture_uncertainty_seconds": frame.capture_uncertainty,
            "capture_interval": (
                [
                    frame.captured - frame.capture_uncertainty,
                    frame.captured + frame.capture_uncertainty,
                ]
                if frame.capture_time_known and frame.capture_uncertainty is not None
                else None
            ),
            "timing_note": frame.timing_note,
            "source_monotonic": frame.source_monotonic,
            "clock_uncertainty_seconds": frame.clock_uncertainty,
            "clock_owner": frame.clock_owner,
            "source": frame.source,
            "source_frame_id": frame.source_frame_id,
            "source_kind": source.kind,
            "source_label": source.label,
            "generation": frame.generation,
            "captured": frame.captured,
            "arrived": frame.arrived,
            "width": frame.width,
            "height": frame.height,
            "transformation": dict(frame.transformation),
            "pin": frame.pin,
            "labels": frame.labels,
            "events": frame.events,
            "expires": None if frame.pin else frame.captured + self.retention,
        }

    def browse(
        self,
        *,
        source=None,
        start=0,
        end=float("inf"),
        cursor=0,
        limit=24,
        query="",
        sampling="recent",
    ):
        if sampling not in ("recent", "representative") or (
            sampling == "representative" and cursor
        ):
            raise ToolError("invalid_input")
        self.expire()
        candidates = [
            f
            for f in self.frames.values()
            if (not source or f.source == source)
            and self.overlaps(f, start, end)
            and (not query or query.lower() in " ".join(f.labels + [f.pin]).lower())
        ]
        # Newer arrivals break equal-capture-time ties, but delayed frames must
        # not outrank captures that actually happened later.
        candidates.reverse()
        candidates.sort(key=lambda frame: frame.captured, reverse=True)
        count = len(candidates)
        if sampling == "representative":
            sample_count = min(count, min(24, max(1, limit)))
            if sample_count > 1:
                candidates = [
                    candidates[round(index * (count - 1) / (sample_count - 1))]
                    for index in range(sample_count)
                ]
            else:
                candidates = candidates[:sample_count]
        batch = candidates[max(0, cursor) : max(0, cursor) + min(24, max(1, limit))]
        return {
            "frames": [self.describe(f) for f in batch],
            "next_cursor": cursor + len(batch) if cursor + len(batch) < len(candidates) else None,
            "coverage": "Metadata/time/labels only; no semantic image index",
            "sampling": sampling,
            "matching_frames": count,
            "unsampled_frames": count - len(batch) if sampling == "representative" else 0,
        }

    def associate_speech(self, text, start, end, sources, *, uncertainty=0):
        """Index bounded lexical hints against existing images in a captured interval."""
        if (
            not isinstance(text, str)
            or type(uncertainty) not in (int, float)
            or not math.isfinite(uncertainty)
            or not 0 <= uncertainty <= 10
            or not all(
                type(value) in (int, float) and math.isfinite(value) for value in (start, end)
            )
            or not 0 <= start <= end <= self.clock() + 2
        ):
            raise ToolError("invalid_speech_interval")
        terms = sorted(
            {term for term in re.findall(r"\w+", text[:12000].casefold()) if len(term) <= 80}
        )[:64]
        self.expire()
        pin_usage = sum(frame.size for frame in self.frames.values() if frame.pin)
        for frame in self.frames.values():
            source = self.sources.get(frame.source)
            if (
                source is None
                or not source.enabled
                or source.kind not in {"camera", "screen"}
                or sources.get(frame.source) != frame.generation
                or source.generation != frame.generation
                or not frame.capture_time_known
                or not self.overlaps(frame, start - 2 - uncertainty, end + 2 + uncertainty)
            ):
                continue
            combined = sorted(set(frame.speech_terms).union(terms))[:64]
            size = len(json.dumps(combined).encode()) if combined else 0
            delta = size - frame.speech_metadata_bytes
            if frame.pin and pin_usage + delta > self.pin_bytes:
                frame.speech_index_limited = True
                continue
            if frame.pin:
                pin_usage += delta
            frame.speech_terms = combined
            frame.speech_metadata_bytes = size
            frame.speech_index_limited = False
        self.expire()

    def search(
        self,
        *,
        source=None,
        start=0,
        end=float("inf"),
        query="",
        event_types=(),
        object_labels=(),
        near=None,
    ):
        self.expire()
        terms = set(re.findall(r"\w+", query.casefold()))
        wanted_events, wanted_objects = set(event_types), {x.casefold() for x in object_labels}
        candidates = []
        for frame in self.frames.values():
            if (source and frame.source != source) or not self.overlaps(frame, start, end):
                continue
            kinds = {event["kind"] for event in frame.events}
            objects = {
                event["object_label"].casefold() for event in frame.events if event["object_label"]
            }
            if wanted_events and not wanted_events.intersection(kinds):
                continue
            if wanted_objects and not wanted_objects.intersection(objects):
                continue
            text = " ".join(frame.labels + [frame.pin] + list(objects)).casefold()
            matches = len(
                terms.intersection(set(re.findall(r"\w+", text)).union(frame.speech_terms))
            )
            if terms and not matches:
                continue
            distance = (
                max(0, abs(frame.captured - near) - self.time_radius(frame))
                if near is not None
                else 0
            )
            candidates.append((matches, distance, frame))
        candidates.sort(key=lambda item: (-item[0], item[1], -item[2].sharpness, -item[2].captured))
        return {
            "frames": [
                {
                    **self.describe(frame),
                    "rank": {
                        "text_matches": matches,
                        "distance_seconds": distance if near is not None else None,
                    },
                }
                for matches, distance, frame in candidates[:8]
            ],
            "matching_frames": len(candidates),
            "coverage": "Retained labels, pins, detector events and speech-interval keywords; no OCR or semantic image index. Speech keywords locate nearby existing frames, not visible facts. Unarchived or expired images are unavailable. Thumbnail gradient energy breaks ranking ties; it does not prove legibility.",
        }

    def validate_region(self, frame_id, region):
        frame = self.get(frame_id)
        if (
            not isinstance(region, (list, tuple))
            or len(region) != 4
            or any(type(n) is not int for n in region)
        ):
            raise ToolError("invalid_region")
        x, y, w, h = region
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > frame.width or y + h > frame.height:
            raise ToolError("invalid_region")
        return x, y, w, h

    def image_snapshot(self, frame_id, region=None, *, thumbnail=False):
        frame = self.get(frame_id)
        region = self.validate_region(frame_id, region) if region is not None else None
        return frame.image, frame.thumbnail, region, thumbnail

    @staticmethod
    def encode_image(snapshot):
        original, preview, region, thumbnail = snapshot
        data = preview if thumbnail else original
        image_format = "png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "jpeg"
        if region is not None:
            x, y, w, h = region
            with Image.open(io.BytesIO(original)) as image:
                output = io.BytesIO()
                image_format = "png" if image.format == "PNG" else "jpeg"
                cropped = image.crop((x, y, x + w, y + h))
                cropped.info.clear()
                cropped.save(
                    output,
                    format=image_format.upper(),
                    **({"quality": 95} if image_format == "jpeg" else {}),
                )
                data = output.getvalue()
        return {
            "type": "input_image",
            "image_url": f"data:image/{image_format};base64," + base64.b64encode(data).decode(),
            "detail": "high",
        }

    def image_input(self, frame_id, region=None, *, thumbnail=False):
        return self.encode_image(self.image_snapshot(frame_id, region, thumbnail=thumbnail))

    async def image_input_async(self, frame_id, region=None, *, thumbnail=False):
        snapshot = self.image_snapshot(frame_id, region, thumbnail=thumbnail)
        with self.image_work.slot(frame_id) as encode:
            result = await encode(snapshot, self.encode_image)
        self.get(frame_id)  # Clearing/expiry while the immutable worker runs retires the result.
        return result
