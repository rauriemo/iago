"""Synthetic detector classifications exercise the real recorded-input controller path."""

import copy

import pytest
from pydantic import ValidationError
from test_wave_replay import clip_fixture

from reachy_brain.evals.thumb_clips import (
    NEGATIVES,
    ThumbClip,
    ThumbDataset,
    replay_thumbs,
    score_thumbs,
)


@pytest.mark.features("P10")
@pytest.mark.scenario("THUMB-REPLAY-ACCEPTED-CONTROLLER-TURNS")
@pytest.mark.parametrize(
    "gesture,people,question,expected",
    [
        ("Thumb_Up", 1, 0, "yes"),
        ("Thumb_Down", 1, 0, "no"),
        ("Thumb_Up", 2, 0, None),
        ("Thumb_Up", 1, None, None),
    ],
)
def test_classification_only_counts_when_controller_accepts(
    tmp_path, monkeypatch, gesture, people, question, expected
):
    class SyntheticDetectors:
        version = "synthetic-thumb-tracks"

        def __init__(self, models):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def objects(self, rgb, timestamp):
            return [dict(label="person", confidence=0.99, box=[0, 0, 1, 1])] * people

        def hands(self, rgb, timestamp):
            return [
                dict(
                    gesture=gesture if timestamp >= 101200 else "None",
                    confidence=0.99,
                    side="Right",
                    landmarks=[[0.5, 0.5, 0]] * 21,
                )
            ]

    monkeypatch.setattr("reachy_brain.vision.worker.LocalDetectors", SyntheticDetectors)
    base = clip_fixture(tmp_path).model_dump(exclude={"label", "category"})
    clip = ThumbClip(
        **base,
        label="negative",
        category="neutral",
        camera="synthetic",
        people=people,
        orientation="test",
        distance="test",
        lighting="test",
        question_presented_ms=question,
    )
    result = replay_thumbs(tmp_path, clip, tmp_path)
    if expected:
        assert result["accepted"] == [
            dict(value=expected, question="q/synthetic", turn=1, at_ms=1900)
        ]
    else:
        assert result["accepted"] == []
    assert sum(result["detected_people_counts"].values()) == 31


@pytest.mark.features("P10")
@pytest.mark.scenario("THUMB-REPLAY-EXACT-ACCEPTANCE-GATES")
def test_wrong_polarity_binding_duplicates_and_false_response_thresholds():
    rows = []
    for index in range(60):
        label = "thumb_up" if index < 20 else "thumb_down" if index < 40 else "negative"
        row = dict(
            id=str(index), label=label, accepted=[], labeled_people=1, question_presented_ms=0
        )
        if index % 20 < 18 and index < 40:
            row["accepted"] = [
                dict(value="yes" if index < 20 else "no", question="q/" + str(index), turn=1)
            ]
        rows.append(row)
    assert score_thumbs(rows)["passes_target"]
    for update in (dict(value="no"), dict(question="wrong"), dict(turn=2)):
        changed = copy.deepcopy(rows)
        changed[0]["accepted"][0].update(update)
        assert not score_thumbs(changed)["passes_target"]
    changed = copy.deepcopy(rows)
    changed[0]["accepted"] *= 2
    assert not score_thumbs(changed)["passes_target"]
    rows[40]["accepted"] = [dict(value="yes", question="q/40", turn=1)]
    assert score_thumbs(rows)["passes_target"]
    rows[41]["accepted"] = [dict(value="yes", question="q/41", turn=1)]
    assert not score_thumbs(rows)["passes_target"]
    rows[41]["accepted"] = []
    rows[40]["question_presented_ms"] = None
    assert not score_thumbs(rows)["passes_target"]


@pytest.mark.features("P10")
@pytest.mark.scenario("THUMB-REPLAY-POSITIVE-LABEL-CONTEXT")
def test_positive_labels_require_single_person_question(tmp_path):
    base = clip_fixture(tmp_path).model_dump(exclude={"label", "category"})
    data = dict(
        **base,
        label="thumb_up",
        category="thumb_up",
        camera="synthetic",
        people=1,
        orientation="test",
        distance="test",
        lighting="test",
        question_presented_ms=0,
    )
    ThumbClip.model_validate(data)
    for change in (dict(people=2), dict(question_presented_ms=None), dict(category="thumb_down")):
        with pytest.raises(ValidationError, match="positive_thumb"):
            ThumbClip.model_validate({**data, **change})


@pytest.mark.features("P10")
@pytest.mark.scenario("THUMB-REPLAY-DATASET-COVERAGE")
@pytest.mark.parametrize(
    "mutation", ["duplicate_id", "duplicate_sequence", "missing_class", "unvaried", "unreviewed"]
)
def test_dataset_declarations_cannot_omit_required_coverage(tmp_path, mutation):
    base = clip_fixture(tmp_path).model_dump(exclude={"label", "category"})
    clips = []
    for i in range(60):
        row = copy.deepcopy(base)
        label = "thumb_up" if i < 20 else "thumb_down" if i < 40 else "negative"
        row.update(
            id=str(i),
            label=label,
            category=label if i < 40 else sorted(NEGATIVES)[i % len(NEGATIVES)],
            camera="synthetic",
            people=1,
            orientation=str(i % 2),
            distance=str(i % 2),
            lighting=str(i % 2),
            question_presented_ms=0,
        )
        row["frames"][0]["sha256"] = f"{i:064x}"
        clips.append(row)
    data = dict(
        fixture_kind="real-prerecorded-camera",
        operator="synthetic-schema-test",
        frozen_at="test",
        human_reviewed_labels=True,
        clips=clips,
    )
    ThumbDataset.model_validate(data)  # No files are read and no real-media claim is made.
    if mutation == "duplicate_id":
        clips[1]["id"] = clips[0]["id"]
    elif mutation == "duplicate_sequence":
        clips[1]["frames"] = clips[0]["frames"]
    elif mutation == "missing_class":
        clips[0].update(label="negative", category="neutral")
    elif mutation == "unvaried":
        for row in clips[:20]:
            row["orientation"] = "one"
    else:
        data["human_reviewed_labels"] = False
    with pytest.raises(ValidationError):
        ThumbDataset.model_validate(data)
