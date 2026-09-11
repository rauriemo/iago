"""Bind detector output to the host-owned source before any conversational interpretation."""

import math
import uuid

from reachy_brain.behavior.engine import Event
from reachy_brain.behavior.thumbs import Observation

from .temporal import Presence, Wave


class PerceptionEvents:
    def __init__(self):
        self.identity = None
        self.presence = Presence()
        self.waves = {}
        self.objects = {}
        self.track = uuid.uuid4().hex
        self.last = -math.inf

    def update(self, result, source, *, now):
        if (
            source.kind != "camera"
            or not source.enabled
            or (source.id, source.generation) != (result["source"], result["generation"])
        ):
            return [], None
        at = result["captured"]
        uncertainty = result.get("uncertainty", 0)
        if not math.isfinite(uncertainty) or uncertainty < 0:
            return [], None
        if not math.isfinite(at) or not 0 <= now - at <= 1:
            return [], None
        identity = (source.id, source.generation)
        if identity != self.identity:
            self.__init__()
            self.identity = identity
        if at <= self.last:
            return [], None
        self.last = at
        events = []

        def event(kind, confidence, *, duration=0, details=None):
            return Event(
                uuid.uuid4().hex,
                source.id,
                kind,
                at,
                confidence,
                track=self.track,
                duration=duration,
                generation=source.generation,
                detector=result["detector"],
                details=details or {},
            )

        scene = result.get("scene_change")
        if scene and not result["moving"]:
            events.append(
                event(
                    "scene_changed",
                    scene["score"],
                    duration=scene["duration"],
                    details={"spatial_difference": scene["spatial_difference"]},
                )
            )
        objects = result["objects"] if at - result["objects_at"] <= 0.5 else []
        people = [obj for obj in objects if obj["label"] == "person" and obj["confidence"] >= 0.6]
        transition = self.presence.update(bool(people), at)
        if transition:
            events.append(
                event(
                    transition,
                    max((p["confidence"] for p in people), default=1),
                    duration=0.7 if people else 3,
                )
            )
            if not people:
                self.track = uuid.uuid4().hex
                self.waves.clear()
        labels = {obj["label"]: obj for obj in objects if obj["confidence"] >= 0.6}
        for label, obj in labels.items():
            state = self.objects.setdefault(label, {"first": at, "last": at, "announced": False})
            state["last"] = at
            if not state["announced"] and at - state["first"] >= 0.5:
                state["announced"] = True
                events.append(
                    event(
                        "object_appeared",
                        obj["confidence"],
                        duration=at - state["first"],
                        details={"label": label},
                    )
                )
        for label, state in list(self.objects.items()):
            if at - state["last"] >= 3:
                if state["announced"]:
                    events.append(
                        event("object_disappeared", 1, duration=3, details={"label": label})
                    )
                del self.objects[label]
        hands = result["hands"]
        classifications = []
        associated = len(people) == 1 and not result["moving"] and uncertainty <= 0.1
        visible_tracks = set()
        for index, hand in enumerate(hands):
            landmarks = hand["landmarks"]
            wrist = landmarks[0]
            scale = math.hypot(landmarks[9][0] - wrist[0], landmarks[9][1] - wrist[1])
            if associated:
                x, y, width, height = people[0]["box"]
                associated = (
                    x - 0.1 <= wrist[0] <= x + width + 0.1
                    and y - 0.1 <= wrist[1] <= y + height + 0.1
                )
            key = (hand["side"], index)
            visible_tracks.add(key)
            wave = self.waves.setdefault(key, Wave())
            if wave.update(
                at,
                wrist[0],
                scale,
                open_palm=hand["gesture"] == "Open_Palm",
                confident=hand["confidence"] >= 0.8 and associated,
                moving=result["moving"],
            ):
                events.append(event("wave_detected", hand["confidence"], duration=0.6))
            if hand["gesture"] in {"Thumb_Up", "Thumb_Down"}:
                classifications.append(
                    (
                        "thumb_up" if hand["gesture"] == "Thumb_Up" else "thumb_down",
                        hand["confidence"],
                    )
                )
        self.waves = {key: value for key, value in self.waves.items() if key in visible_tracks}
        classes = {value for value, _ in classifications}
        gesture = "conflict" if len(classes) > 1 else next(iter(classes)) if classes else "neutral"
        confidence = min((score for _, score in classifications), default=1)
        observation = Observation(
            uuid.uuid4().hex,
            source.id,
            source.generation,
            source.kind,
            at,
            gesture,
            confidence,
            len(people),
            associated,
            uncertainty,
            detector=result["detector"],
        )
        return events, observation
