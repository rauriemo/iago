"""Validate continuity of controller gesture exports without claiming camera accuracy."""

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from .screen_grounding import StrictRecord
from .speech_observation import compare_activity

Identifier = Annotated[str, Field(min_length=1, max_length=128)]


class GestureEvent(StrictRecord):
    sequence: int = Field(ge=1)
    epoch: int = Field(ge=0)
    seconds: float = Field(ge=0)
    slot: Identifier


class AcceptedGesture(GestureEvent):
    kind: Literal["accepted"]
    session: Identifier
    question: Identifier
    source: Identifier
    value: Literal["yes", "no"]
    gesture: Literal["thumb_up", "thumb_down"]
    turn: int = Field(ge=0)
    generation: int = Field(ge=0)


class SupersededGesture(GestureEvent):
    kind: Literal["superseded"]


class GestureSnapshot(StrictRecord):
    available: Literal[True] = True
    owner: Identifier
    elapsed_seconds: float = Field(ge=0)
    counts: dict[Literal["accepted", "superseded"], int] = Field(min_length=2, max_length=2)
    samples: list[Annotated[AcceptedGesture | SupersededGesture, Field(discriminator="kind")]] = (
        Field(max_length=512)
    )
    sample_limit: Literal[512]
    dropped_samples: int = Field(ge=0)
    scope: str = Field(max_length=2000)


def compare_gestures(start: GestureSnapshot, end: GestureSnapshot):
    result = compare_activity(start, end)
    # Reuse identical bounded sequence/count checks, retaining gesture-specific claims.
    result.pop("physical_echo_validated")
    result.pop("limitation")
    return {
        **result,
        "physical_gesture_validated": False,
        "release_validated": False,
        "limitation": (
            "Complete backend event interval only. Initial acceptances and supersessions remain "
            "separate. Requires independent live camera labels, question context, feedback and "
            "recording provenance; zero or valid event counts cannot establish gesture accuracy."
        ),
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("start", "end", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    snapshots = []
    for path in (args.start, args.end):
        with path.open("rb") as source:
            data = source.read(512 * 1024 + 1)
        if len(data) > 512 * 1024:
            raise ValueError("gesture_export_size_limit")
        snapshots.append(GestureSnapshot.model_validate_json(data))
    result = compare_gestures(*snapshots)
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
