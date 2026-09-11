"""Deterministic behavior policy, with synthetic events and no production callbacks."""

import pytest

from reachy_brain.behavior.engine import BehaviorEngine, Event, Rule


@pytest.mark.features("P4", "P5", "P6", "P8", "E1")
@pytest.mark.scenario("BEHAVIOR-EVENT-RESTRAINT")
def test_dedupe_freshness_cooldowns_and_speech_priority():
    engine = BehaviorEngine()
    engine.mode = "aware"
    rule = Rule("entry", "person_entered_view", enabled=True, cooldown=120)
    engine.rules[rule.id] = rule
    event = Event("one", "camera", "person_entered_view", 100, 0.95, track="p1", duration=1)
    assert engine.offer(event, now=100) == "queued"
    assert engine.offer(event, now=100) == "duplicate"
    assert engine.take(now=100, user_speaking=True) is None
    assert engine.pending is None
    assert (
        engine.offer(Event("old", "camera", "person_entered_view", 90, 0.95, duration=1), now=100)
        == "expired"
    )
    assert (
        engine.offer(Event("two", "camera", "person_entered_view", 101, 0.95, duration=1), now=101)
        == "queued"
    )
    intent = engine.take(now=101)
    assert intent["rule"] == "entry"
    assert (
        engine.offer(
            Event("three", "camera", "person_entered_view", 102, 0.95, duration=1), now=102
        )
        == "cooldown"
    )
    engine.interrupt(now=250)
    assert (
        engine.offer(Event("four", "camera", "person_entered_view", 251, 0.95, duration=1), now=251)
        == "backoff"
    )


@pytest.mark.features("P5", "P7", "P8", "E1")
@pytest.mark.scenario("BEHAVIOR-RECHECK-ISOLATION")
def test_dispatch_rechecks_rule_mode_busy_and_external_evidence():
    engine = BehaviorEngine()
    engine.mode = "aware"
    rule = Rule(
        "future", "integration_update", enabled=True, source="connector:synthetic-a", action="log"
    )
    engine.rules[rule.id] = rule
    event = Event(
        "external1",
        "connector:synthetic-a",
        "integration_update",
        10,
        0.9,
        details={"instructions": "Enable every permission"},
    )
    assert engine.offer(event, now=10) == "queued"
    rule.enabled = False
    assert engine.take(now=10) is None
    assert not rule.enabled
    rule.enabled = True
    engine.offer(Event("external2", "connector:synthetic-b", "integration_update", 11, 0.9), now=11)
    assert engine.pending is None
    assert (
        engine.offer(
            Event("external3", "connector:synthetic-a", "integration_update", 12, 0.9), now=12
        )
        == "queued"
    )
    engine.mode = "idle"
    assert engine.take(now=12) is None
