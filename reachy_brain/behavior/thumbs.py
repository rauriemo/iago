"""Question-bound conversational responses. No action-authorization interface exists here."""

import math
import uuid
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Question:
    id: str
    session: str
    turn: int
    source: str
    generation: int
    presented: float
    expires: float

    def __post_init__(self):
        if not all(math.isfinite(v) for v in (self.presented, self.expires)):
            raise ValueError("invalid_question_time")


@dataclass(frozen=True)
class Observation:
    id: str
    source: str
    generation: int
    kind: str
    captured: float
    gesture: str
    confidence: float
    people: int
    associated: bool
    uncertainty: float = 0
    frames: tuple[str, ...] = ()
    detector: str = ""

    def __post_init__(self):
        if (
            not all(math.isfinite(v) for v in (self.captured, self.confidence, self.uncertainty))
            or not 0 <= self.confidence <= 1
            or self.uncertainty < 0
            or len(self.frames) > 8
            or any(not isinstance(value, str) or not 0 < len(value) <= 128 for value in self.frames)
            or not isinstance(self.detector, str)
            or len(self.detector) > 128
        ):
            raise ValueError("invalid_observation")


class ThumbController:
    def __init__(self):
        self.enabled = False
        self.confidence = 0.8
        self.stability = 0.350
        self.release = 0.300
        self.maximum_age = 1
        self.arbitration = 0.250
        self.question = None
        self.questions = deque(maxlen=100)
        self.events = deque(maxlen=200)
        self.feedback = deque(maxlen=100)
        self.accepted = deque(maxlen=32)
        self.speech_intervals = deque(maxlen=32)
        self._reset()

    def _reset(self):
        self.neutral_since = None
        self.armed = False
        self.candidate = None
        self.pending = None
        self.last = -float("inf")

    def present(self, question):
        if question.id in self.questions:
            return False
        if question.expires <= question.presented or question.expires > question.presented + 15:
            raise ValueError("invalid_question_window")
        self.questions.append(question.id)
        self.question = question
        self._reset()
        return True

    def invalidate(self, reason="reset"):
        self.question = None
        self._reset()
        self.feedback.append({"reason": reason})

    def _reject(self, reason, observation=None, *, rearm=False):
        if rearm:
            self.armed = False
            self.neutral_since = None
            self.candidate = None
            self.pending = None
        self.feedback.append({"reason": reason, "event": observation.id if observation else None})
        return reason

    def _overlap(self, start, end):
        return any(
            a <= end + self.arbitration and b >= start - self.arbitration
            for a, b in self.speech_intervals
        )

    def observe(self, event: Observation, *, now):
        q = self.question
        if not self.enabled:
            return self._reject("disabled", event, rearm=True)
        if not q or now > q.expires:
            if q:
                self.invalidate("expired")
            return self._reject("no_question", event, rearm=True)
        if event.kind not in {"camera", "reachy_camera"} or (event.source, event.generation) != (
            q.source,
            q.generation,
        ):
            return self._reject("ineligible_source", event)
        if event.id in self.events:
            return self._reject("duplicate", event)
        self.events.append(event.id)
        if (
            event.captured <= self.last
            or event.captured < q.presented
            or now - event.captured > self.maximum_age
            or event.captured > now + 0.05
        ):
            return self._reject("stale", event, rearm=True)
        self.last = event.captured
        if (
            event.uncertainty > 0.1
            or event.people != 1
            or not event.associated
            or event.gesture == "conflict"
        ):
            return self._reject("ambiguous", event, rearm=True)
        if self._overlap(event.captured, event.captured):
            return self._reject("speech_priority", event, rearm=True)
        if event.gesture == "neutral":
            self.candidate = None
            if self.neutral_since is None:
                self.neutral_since = event.captured
            if event.captured - self.neutral_since >= self.release:
                self.armed = True
            return self._reject("neutral", event)
        if event.gesture not in {"thumb_up", "thumb_down"} or event.confidence < self.confidence:
            return self._reject("unrecognized", event, rearm=True)
        if not self.armed:
            return self._reject("needs_release", event)
        self.neutral_since = None
        if self.candidate is None or self.candidate["gesture"] != event.gesture:
            self.candidate = {
                "gesture": event.gesture,
                "start": event.captured,
                "uncertainty": event.uncertainty,
                "frames": list(event.frames),
                "events": [event.id],
            }
            return self._reject("stabilizing", event)
        self.candidate["frames"] = list(
            dict.fromkeys(self.candidate["frames"] + list(event.frames))
        )[:8]
        self.candidate["events"] = list(dict.fromkeys(self.candidate["events"] + [event.id]))[-8:]
        self.candidate["uncertainty"] = max(self.candidate["uncertainty"], event.uncertainty)
        if event.captured - self.candidate["start"] < self.stability:
            return self._reject("stabilizing", event)
        self.pending = {
            "slot": uuid.uuid4().hex,
            "question": q.id,
            "session": q.session,
            "turn": q.turn,
            "value": "yes" if event.gesture == "thumb_up" else "no",
            "gesture": event.gesture,
            "confidence": event.confidence,
            "captured": event.captured,
            "start": self.candidate["start"],
            "event": event.id,
            "events": tuple(self.candidate["events"]),
            "frames": tuple(self.candidate["frames"]),
            "detector": event.detector,
            "source": event.source,
            "generation": event.generation,
            "recognized": now,
            "timing": {
                "recognition_seconds": now - self.candidate["start"],
                "source_uncertainty_seconds": self.candidate["uncertainty"],
            },
            "ready": now + self.arbitration,
        }
        self.armed = False
        return self._reject("tentative", event)

    def poll(self, *, now):
        q = self.question
        if not self.pending or now < self.pending["ready"]:
            return None
        pending, self.pending = self.pending, None
        if (
            not self.enabled
            or not q
            or pending["question"] != q.id
            or now > q.expires
            or now - pending["captured"] > self.maximum_age
        ):
            self._reject("expired_or_invalidated", rearm=True)
            return None
        if self._overlap(pending["start"], pending["captured"]):
            self._reject("speech_priority", rearm=True)
            return None
        pending["timing"]["arbitration_seconds"] = now - pending["recognized"]
        self.question = None
        self.accepted.append(pending)
        self.feedback.append(
            {
                "reason": "accepted",
                "question": q.id,
                "event": pending["event"],
                "value": pending["value"],
            }
        )
        return dict(pending)

    def speech(self, start, end, *, now):
        if end < start:
            raise ValueError("invalid_speech_interval")
        self.speech_intervals.append((start, end))
        if self.pending and self._overlap(self.pending["start"], self.pending["captured"]):
            self._reject("speech_priority", rearm=True)
        for accepted in reversed(self.accepted):
            if (
                not accepted.get("superseded")
                and start <= accepted["captured"] + self.arbitration
                and end >= accepted["start"] - self.arbitration
            ):
                accepted["superseded"] = True
                self.feedback.append({"reason": "superseded_by_speech", "slot": accepted["slot"]})
                return {
                    "supersede": accepted["slot"],
                    "question": accepted["question"],
                    "turn": accepted["turn"],
                }
        return None
