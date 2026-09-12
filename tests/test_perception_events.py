"""Synthetic detector outputs exercise provenance binding, not model accuracy."""

from types import SimpleNamespace

import pytest

from reachy_brain.vision.events import PerceptionEvents


@pytest.mark.features("P1", "P2", "P3", "P10", "V9")
@pytest.mark.scenario("PERCEPTION-EVENT-PROVENANCE")
def test_source_binding_rejects_screens_and_old_generations():
    interpreter = PerceptionEvents()
    source = SimpleNamespace(id="camera", kind="camera", generation=2, enabled=True)
    result = {
        "source": "camera",
        "generation": 2,
        "captured": 10,
        "objects_at": 10,
        "objects": [],
        "hands": [],
        "moving": False,
        "detector": "synthetic",
    }
    events, observation = interpreter.update(result, source, now=10)
    assert not events
    assert observation.source == "camera" and observation.generation == 2
    assert not observation.associated
    source.kind = "screen"
    assert interpreter.update(result, source, now=10) == ([], None)
    source.kind = "camera"
    source.generation = 3
    assert interpreter.update(result, source, now=10) == ([], None)
    source.generation = 2
    assert interpreter.update(result, source, now=12) == ([], None)


@pytest.mark.features("P10")
@pytest.mark.scenario("PERCEPTION-CONFLICTING-HANDS")
def test_conflicting_hands_remain_ambiguous():
    source = SimpleNamespace(id="camera", kind="camera", generation=1, enabled=True)
    hands = [
        {"gesture": gesture, "confidence": 0.95, "side": side, "landmarks": [[0.5, 0.5, 0]] * 21}
        for gesture, side in [("Thumb_Up", "Left"), ("Thumb_Down", "Right")]
    ]
    result = {
        "source": "camera",
        "generation": 1,
        "captured": 10,
        "objects_at": 10,
        "objects": [{"label": "person", "confidence": 0.95, "box": [0, 0, 1, 1]}],
        "hands": hands,
        "moving": False,
        "detector": "synthetic",
    }
    _, observation = PerceptionEvents().update(result, source, now=10)
    assert observation.gesture == "conflict"


@pytest.mark.features("P3", "P10", "D2", "D3")
@pytest.mark.scenario("ROBOT-UNQUALIFIED-GESTURE-TIMING")
def test_unknown_capture_timing_cannot_associate_gestures():
    source = SimpleNamespace(id="camera", kind="camera", generation=1, enabled=True)
    result = {
        "source": "camera",
        "generation": 1,
        "captured": 10,
        "objects_at": 10,
        "objects": [{"label": "person", "confidence": 0.99, "box": [0, 0, 1, 1]}],
        "hands": [
            {
                "gesture": "Thumb_Up",
                "confidence": 0.99,
                "side": "Left",
                "landmarks": [[0.5, 0.5, 0]] * 21,
            }
        ],
        "moving": False,
        "detector": "synthetic",
        "uncertainty": 1.0,
    }
    _, observation = PerceptionEvents().update(result, source, now=10)
    assert observation.gesture == "thumb_up"
    assert not observation.associated and observation.uncertainty == 1.0
    result["uncertainty"] = float("nan")
    assert PerceptionEvents().update(result, source, now=10) == ([], None)


@pytest.mark.features("V5", "P3", "P10")
@pytest.mark.scenario("DETECTOR-SEQUENCE-EVENT-REPLAY")
def test_replayed_sequence_cannot_accumulate_new_temporal_evidence():
    interpreter = PerceptionEvents()
    source = SimpleNamespace(id="camera", kind="camera", generation=0, enabled=True)

    def result(at, sequence):
        return {
            "source": "camera",
            "generation": source.generation,
            "captured": at,
            "sequence": sequence,
            "objects_at": at,
            "objects": [],
            "hands": [],
            "moving": False,
            "detector": "synthetic",
        }

    assert interpreter.update(result(10, 2), source, now=10)[1] is not None
    for sequence in (2, 1, None, True, "private"):
        assert interpreter.update(result(10.1, sequence), source, now=10.1) == ([], None)
        assert interpreter.last == 10
    assert interpreter.update(result(10.2, 3), source, now=10.2)[1] is not None
    source.generation = 1
    assert interpreter.update(result(10.3, 1), source, now=10.3)[1] is not None
