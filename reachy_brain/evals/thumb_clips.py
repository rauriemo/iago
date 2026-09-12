"""Recorded camera inputs scored at the actual question-response controller boundary."""

from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from reachy_brain.behavior.thumbs import Question, ThumbController

from .wave_clips import RecordedClip, replay_clip

NEGATIVES = {
    "neutral",
    "open_hand",
    "wave",
    "pointing",
    "drawing",
    "phone_use",
    "camera_motion",
    "ambiguous_people",
}


class ThumbClip(RecordedClip):
    label: Literal["thumb_up", "thumb_down", "negative"]
    category: Literal[
        "thumb_up",
        "thumb_down",
        "neutral",
        "open_hand",
        "wave",
        "pointing",
        "drawing",
        "phone_use",
        "camera_motion",
        "ambiguous_people",
    ]
    camera: str = Field(min_length=1, max_length=256)
    people: int = Field(ge=0, le=8)
    orientation: str = Field(min_length=1, max_length=128)
    distance: str = Field(min_length=1, max_length=128)
    lighting: str = Field(min_length=1, max_length=128)
    question_presented_ms: int | None = Field(ge=0, le=7000)

    @model_validator(mode="after")
    def labels(self):
        if any(
            not getattr(self, field).strip()
            for field in ("camera", "orientation", "distance", "lighting")
        ):
            raise ValueError("nonblank_recording_labels_required")
        if self.label != "negative" and (
            self.category != self.label or self.people != 1 or self.question_presented_ms is None
        ):
            raise ValueError("positive_thumb_requires_one_person_and_question")
        if self.label == "negative" and self.category not in NEGATIVES:
            raise ValueError("negative_category_required")
        if (
            self.question_presented_ms is not None
            and self.question_presented_ms >= self.frames[-1].at_ms
        ):
            raise ValueError("question_must_occur_in_clip")
        return self


class ThumbDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    fixture_kind: Literal["real-prerecorded-camera"]
    operator: str = Field(min_length=1, max_length=128)
    frozen_at: str = Field(min_length=1, max_length=128)
    human_reviewed_labels: bool
    clips: list[ThumbClip] = Field(min_length=60, max_length=200)

    @model_validator(mode="after")
    def coverage(self):
        if (
            not self.human_reviewed_labels
            or not self.operator.strip()
            or not self.frozen_at.strip()
        ):
            raise ValueError("reviewed_frozen_labels_required")
        if len({c.id for c in self.clips}) != len(self.clips):
            raise ValueError("duplicate_clip_ids")
        if len({tuple((f.sha256, f.at_ms) for f in c.frames) for c in self.clips}) != len(
            self.clips
        ):
            raise ValueError("duplicate_recorded_sequences")
        for label in ("thumb_up", "thumb_down", "negative"):
            if sum(c.label == label for c in self.clips) < 20:
                raise ValueError("twenty_per_thumb_class_and_negatives_required")
        if not NEGATIVES <= {c.category for c in self.clips}:
            raise ValueError("negative_category_coverage_required")
        for label in ("thumb_up", "thumb_down"):
            for field in ("orientation", "distance", "lighting"):
                if len({getattr(c, field) for c in self.clips if c.label == label}) < 2:
                    raise ValueError("varied_positive_conditions_required")
        return self


def replay_thumbs(root: Path, clip: ThumbClip, models: Path):
    controller = ThumbController()
    controller.enabled = True
    presented = False
    accepted = []
    feedback, people = Counter(), Counter()

    def consume(observation, now, source):
        nonlocal presented
        if (
            clip.question_presented_ms is not None
            and not presented
            and now >= 100 + clip.question_presented_ms / 1000
        ):
            at = 100 + clip.question_presented_ms / 1000
            controller.present(
                Question(
                    "q/" + clip.id, "recorded-eval", 1, source.id, source.generation, at, at + 15
                )
            )
            presented = True
        if observation is not None:
            people[str(observation.people)] += 1
            feedback[controller.observe(observation, now=now)] += 1
        response = controller.poll(now=now)
        if response is not None:
            accepted.append(
                dict(
                    value=response["value"],
                    question=response["question"],
                    turn=response["turn"],
                    at_ms=round((now - 100) * 1000),
                )
            )

    result = replay_clip(root, clip, models, observation_consumer=consume)
    # No synthetic after-clip poll: arbitration must complete inside observed media.
    return {
        **result,
        "accepted": accepted,
        "feedback_counts": dict(feedback),
        "detected_people_counts": dict(people),
        "labeled_people": clip.people,
        "question_presented_ms": clip.question_presented_ms,
    }


def score_thumbs(rows):
    groups = {}
    wrong = duplicates = false = invalid_context = 0
    for row in rows:
        accepted = row["accepted"]
        invalid_context += sum(
            a["question"] != "q/" + row["id"]
            or a["turn"] != 1
            or row["question_presented_ms"] is None
            or row["labeled_people"] != 1
            for a in accepted
        )
        duplicates += max(0, len(accepted) - 1)
        if row["label"] == "negative":
            false += len(accepted)
        else:
            expected = "yes" if row["label"] == "thumb_up" else "no"
            wrong += sum(a["value"] != expected for a in accepted)
    for label, expected in (("thumb_up", "yes"), ("thumb_down", "no")):
        subset = [row for row in rows if row["label"] == label]
        correct = [
            row
            for row in subset
            if len(row["accepted"]) == 1
            and row["accepted"][0]["value"] == expected
            and row["accepted"][0]["question"] == "q/" + row["id"]
            and row["accepted"][0]["turn"] == 1
        ]
        groups[label] = dict(
            count=len(subset),
            correct=len(correct),
            recall=len(correct) / len(subset) if subset else 0,
            missed_ids=[row["id"] for row in subset if row not in correct],
        )
    negatives = sum(row["label"] == "negative" for row in rows)
    return dict(
        groups=groups,
        negatives=negatives,
        false_responses=false,
        wrong_polarity=wrong,
        duplicate_responses=duplicates,
        invalid_context_responses=invalid_context,
        passes_target=negatives >= 20
        and false <= 1
        and wrong == 0
        and duplicates == 0
        and invalid_context == 0
        and all(g["count"] >= 20 and g["correct"] * 10 >= g["count"] * 9 for g in groups.values()),
    )
