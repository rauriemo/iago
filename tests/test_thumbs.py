"""Synthetic controller inputs; these do not establish real gesture recognition accuracy."""

import pytest

from reachy_brain.behavior.thumbs import Observation, Question, ThumbController


def controller():
    c = ThumbController()
    c.enabled = True
    c.present(Question("q", "session", 3, "camera", 1, 10, 25))
    return c


def observe(c, at, base_gesture="neutral", **changes):
    data = dict(
        id=str(at),
        source="camera",
        generation=1,
        kind="camera",
        captured=at,
        gesture=base_gesture,
        confidence=0.95,
        people=1,
        associated=True,
    )
    data.update(changes)
    return c.observe(Observation(**data), now=at)


@pytest.mark.features("P10")
@pytest.mark.scenario("THUMBS-HOLD-REARM")
def test_held_before_question_and_two_questions_require_release():
    c = controller()
    assert observe(c, 10, "thumb_up") == "needs_release"
    assert observe(c, 10.5, "thumb_up") == "needs_release"
    observe(c, 11)
    observe(c, 11.31)
    observe(c, 11.4, "thumb_up")
    assert observe(c, 11.76, "thumb_up") == "tentative"
    assert c.poll(now=11.9) is None
    answer = c.poll(now=12.02)
    assert (answer["value"], answer["question"], answer["turn"]) == ("yes", "q", 3)
    assert c.poll(now=12.1) is None
    c.present(Question("q2", "session", 4, "camera", 1, 12.1, 27.1))
    assert observe(c, 12.2, "thumb_up") == "needs_release"
    observe(c, 12.4)
    observe(c, 12.71)
    observe(c, 12.8, "thumb_down")
    observe(c, 13.16, "thumb_down")
    assert c.poll(now=13.42)["value"] == "no"
    assert len(c.accepted) == 2


@pytest.mark.features("P10")
@pytest.mark.scenario("THUMBS-PROVENANCE-CONFLICT")
@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "screen"},
        {"kind": "upload"},
        {"kind": "history"},
        {"generation": 0},
        {"people": 2},
        {"associated": False},
        {"gesture": "conflict"},
        {"confidence": 0.79},
    ],
)
def test_forbidden_or_uncertain_observations_cannot_answer(changes):
    c = controller()
    observe(c, 10)
    observe(c, 10.31)
    observe(c, 10.4, "thumb_up", **changes)
    observe(c, 10.8, "thumb_up", **changes)
    assert c.poll(now=11.1) is None
    assert len(c.accepted) == 0


@pytest.mark.features("P10")
@pytest.mark.scenario("THUMBS-SPEECH-ORDER")
@pytest.mark.parametrize("ordering", range(5))
def test_overlapping_speech_wins_in_five_delivery_orders(ordering):
    c = controller()
    observe(c, 10)
    observe(c, 10.31)
    if ordering == 0:
        c.speech(10.4, 11, now=10.4)
    observe(c, 10.4, "thumb_up")
    if ordering == 1:
        c.speech(10.4, 11, now=10.6)
    observe(c, 10.76, "thumb_up")
    if ordering == 2:
        c.speech(10.4, 11, now=10.8)
    if ordering == 3:
        c.speech(10.4, 11, now=11.02)
    result = c.poll(now=11.02)
    if ordering == 4:
        assert result is not None
        replacement = c.speech(10.4, 11, now=11.2)
        assert replacement["supersede"] == result["slot"]
    else:
        assert result is None
    assert len([r for r in c.accepted if not r.get("superseded")]) == 0


@pytest.mark.features("P10")
@pytest.mark.scenario("THUMBS-INVALIDATION-ORDER")
@pytest.mark.parametrize("stage", range(5))
@pytest.mark.parametrize("action", ["stop", "end", "camera_loss", "project_reset", "replace"])
def test_invalidation_and_question_replacement_at_five_stages(stage, action):
    c = controller()
    steps = [
        lambda: observe(c, 10),
        lambda: observe(c, 10.31),
        lambda: observe(c, 10.4, "thumb_up"),
        lambda: observe(c, 10.76, "thumb_up"),
        lambda: None,
    ]
    for index, step in enumerate(steps):
        if index == stage:
            if action == "replace":
                c.present(Question("replacement", "session", 4, "camera", 1, 10.8, 25.8))
            else:
                c.invalidate(action)
        step()
    assert c.poll(now=11.02) is None
    assert len(c.accepted) == 0


@pytest.mark.features("P10")
@pytest.mark.scenario("THUMBS-STALE-DISABLED-EXPIRY")
def test_stale_events_disabled_setting_and_expiry():
    c = controller()
    observe(c, 10)
    observe(c, 10.31)
    assert observe(c, 10.2, "thumb_up") == "stale"
    assert observe(c, 10.8, "thumb_up") == "needs_release"
    c.enabled = False
    assert observe(c, 11, "thumb_up") == "disabled"
    c.enabled = True
    assert observe(c, 11.4, "thumb_up") == "needs_release"
    assert observe(c, 26, "thumb_up") == "no_question"
    assert c.poll(now=26.3) is None
    assert not c.accepted
