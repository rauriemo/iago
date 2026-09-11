"""Single pending proactive intent. Events provide evidence, never authorization."""

import math
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from typing import Literal

from reachy_brain.integrations.registry import bounded


@dataclass(frozen=True)
class Event:
    id: str
    source: str
    kind: str
    captured: float
    confidence: float
    track: str = ""
    duration: float = 0
    generation: int = 0
    frames: tuple[str, ...] = ()
    detector: str = ""
    details: dict = field(default_factory=dict)

    def __post_init__(self):
        if not all(math.isfinite(v) for v in (self.captured, self.confidence, self.duration)):
            raise ValueError("invalid_event_time")
        if not 0 <= self.confidence <= 1 or self.duration < 0:
            raise ValueError("invalid_event_confidence")
        bounded(asdict(self), 8192, 30)


@dataclass
class Rule:
    id: str
    event: str
    enabled: bool = False
    source: str = ""
    label: str = ""
    confidence: float = 0.8
    persistence: float = 0
    cooldown: float = 120
    track_cooldown: float = 120
    expiry: float = 5
    modes: tuple[str, ...] = ("aware", "conversation")
    action: Literal["greet", "log", "bookmark"] = "greet"
    prompt: str = "Offer a brief greeting grounded in the current event."
    allow_startup: bool = False

    def __post_init__(self):
        if self.action not in {"greet", "log", "bookmark"}:
            raise ValueError("invalid_behavior_action")
        if not 0 <= self.confidence <= 1 or not all(
            math.isfinite(v) and v >= 0
            for v in (self.persistence, self.cooldown, self.track_cooldown, self.expiry)
        ):
            raise ValueError("invalid_rule_threshold")
        if len(self.prompt) > 2000 or len(self.id) > 100:
            raise ValueError("rule_limit")


class BehaviorEngine:
    def __init__(self):
        self.mode = "idle"
        self.spontaneous = True
        self.quiet = False
        self.backoff_until = 0
        self.rules = {
            "entry": Rule("entry", "person_entered_view", persistence=0.5),
            "wave": Rule("wave", "wave_detected", cooldown=20, track_cooldown=20),
            "objects": Rule("objects", "object_appeared", action="log"),
            "scene": Rule("scene", "scene_changed", action="bookmark"),
        }
        self.seen = OrderedDict()
        self.last = {}
        self.pending = None
        self.log = deque(maxlen=200)

    def _reason(self, rule, event, now, *, cooldowns=True, origin_mode=None):
        if not rule.enabled:
            return "disabled"
        if self.mode == "idle" or (origin_mode or self.mode) not in rule.modes:
            return "mode"
        if self.quiet or not self.spontaneous:
            return "quiet"
        if now < self.backoff_until:
            return "backoff"
        if event.captured > now + 0.1 or now - event.captured > rule.expiry:
            return "expired"
        if event.confidence < rule.confidence or event.duration < rule.persistence:
            return "confidence_or_persistence"
        if event.details.get("startup") and not rule.allow_startup:
            return "startup"
        if rule.source and event.source != rule.source:
            return "source"
        if rule.label and event.details.get("label") != rule.label:
            return "label"
        if cooldowns and now - self.last.get((rule.id, ""), -math.inf) < rule.cooldown:
            return "cooldown"
        if (
            cooldowns
            and event.track
            and now - self.last.get((rule.id, event.track), -math.inf) < rule.track_cooldown
        ):
            return "cooldown"
        return ""

    def _record(self, event, reason, now):
        self.log.append(
            {
                "event": event.id,
                "source": event.source,
                "kind": event.kind,
                "at": now,
                "decision": reason,
            }
        )
        return reason

    def offer(self, event: Event, *, now):
        key = (event.source, event.generation, event.id)
        if key in self.seen:
            return self._record(event, "duplicate", now)
        self.seen[key] = now
        while len(self.seen) > 1000:
            self.seen.popitem(last=False)
        reason = "no_rule"
        for rule in self.rules.values():
            if rule.event != event.kind:
                continue
            reason = self._reason(rule, event, now)
            if reason:
                continue
            if self.pending:
                old = self.pending["event"]
                old_rule = self.rules.get(self.pending["rule"])
                if old_rule and not self._reason(old_rule, old, now):
                    return self._record(event, "coalesced", now)
            self.pending = {"event": event, "rule": rule.id, "configuration": asdict(rule)}
            return self._record(event, "queued", now)
        return self._record(event, reason, now)

    def take(self, *, now, user_speaking=False, output_busy=False):
        pending, self.pending = self.pending, None
        if not pending:
            return None
        event = pending["event"]
        rule = self.rules.get(pending["rule"])
        reason = (
            "configuration_changed"
            if not rule or asdict(rule) != pending["configuration"]
            else self._reason(rule, event, now)
        )
        if user_speaking or output_busy:
            reason = "user_priority" if user_speaking else "output_busy"
        if reason:
            self._record(event, reason, now)
            return None
        self.last[(rule.id, "")] = now
        if event.track:
            self.last[(rule.id, event.track)] = now
        if len(self.last) > 1000:
            self.last = dict(sorted(self.last.items(), key=lambda item: item[1])[-1000:])
        self._record(event, "accepted", now)
        return {
            "rule": rule.id,
            "action": rule.action,
            "prompt": rule.prompt,
            "evidence": asdict(event),
            "configuration": asdict(rule),
            "origin_mode": self.mode,
        }

    def recheck(self, intent, *, now):
        rule = self.rules.get(intent["rule"])
        if not rule or asdict(rule) != intent["configuration"]:
            return False
        return not self._reason(
            rule,
            Event(**intent["evidence"]),
            now,
            cooldowns=False,
            origin_mode=intent.get("origin_mode"),
        )

    def invalidate_source(self, source, generation):
        if (
            self.pending
            and self.pending["event"].source == source
            and self.pending["event"].generation != generation
        ):
            self.pending = None

    def interrupt(self, *, now, backoff=60):
        self.pending = None
        self.backoff_until = now + backoff
