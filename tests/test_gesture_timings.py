"""Synthetic source/receiver clocks exercise initial gesture timing, not physical latency."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from reachy_brain.behavior.thumbs import Observation, Question
from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.evals.workload_timings import summarize
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("P10", "D6")
@pytest.mark.scenario("GESTURE-RECOGNITION-ARBITRATION-COMMIT-TIMING")
@pytest.mark.parametrize("gesture", ["thumb_up", "thumb_down"])
async def test_initial_commit_reports_separate_bounded_intervals(monkeypatch, gesture):
    ticks = [100.0]
    monkeypatch.setattr(
        "reachy_brain.core.conversation.time",
        SimpleNamespace(monotonic=lambda: ticks[0], time=time.time),
    )

    async def send(message):
        if message["type"] == "stop":
            ticks[0] += 0.125

    core = Conversation(
        Settings(_env_file=None, thumb_responses_enabled=True),
        None,
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )
    core.mode = "conversation"
    core.answer = AsyncMock()
    source = core.visual.source(core.connection, "camera", "private-source-title")
    core.thumbs.present(
        Question("q", core.session, core.epoch, source.id, source.generation, 10, 25)
    )
    for i, (at, kind, uncertainty) in enumerate(
        [(10, "neutral", 0), (10.31, "neutral", 0), (10.4, gesture, 0.08), (10.8, gesture, 0.02)]
    ):
        core.thumbs.observe(
            Observation(
                str(i),
                source.id,
                source.generation,
                "camera",
                at,
                kind,
                0.95,
                1,
                True,
                uncertainty=uncertainty,
            ),
            now=at + 0.05,
        )
    response = core.thumbs.poll(now=11.11)
    assert response["timing"]["recognition_seconds"] == pytest.approx(0.45)
    assert response["timing"]["arbitration_seconds"] == pytest.approx(0.26)
    assert response["timing"]["source_uncertainty_seconds"] == 0.08
    try:
        await core.accept_thumb(response)
        await core.task
        activity = core.gesture_activity.snapshot()
        assert activity["counts"] == {"accepted": 1, "superseded": 0}
        accepted = activity["samples"][0]
        for key in (
            "slot",
            "session",
            "question",
            "source",
            "value",
            "gesture",
            "turn",
            "generation",
        ):
            assert accepted[key] == response[key]
        assert "private-source-title" not in str(activity)
        snapshot = core.gesture_timings.snapshot()
        assert snapshot["total"] == 3
        values = {row["operation"]: row["seconds"] for row in snapshot["samples"]}
        assert values[gesture + "_recognition_observed"] == pytest.approx(0.45)
        assert values[gesture + "_arbitration"] == pytest.approx(0.26)
        assert values[gesture + "_commit"] == 0.125
        assert "private-source-title" not in str(snapshot)
        report = summarize([{"owner": "application", "timings": {"gesture": snapshot}}])
        assert len(report["distributions"]) == 3 and report["status"] == "partial_report"
        assert not report["acceptance_pass"]
        assert report["max_gesture_source_uncertainty_seconds"] == 0.08
        await core.accept_thumb(response)
        assert core.gesture_timings.snapshot()["total"] == 3
        assert core.gesture_activity.snapshot()["counts"]["accepted"] == 1
        await core.speech_onset(10.5)
        superseded = core.gesture_activity.snapshot()
        assert superseded["counts"] == {"accepted": 1, "superseded": 1}
        assert superseded["samples"][-1]["slot"] == response["slot"]
    finally:
        await core.stop()
        await core.executor.close()


@pytest.mark.features("P10", "D6")
@pytest.mark.scenario("GESTURE-TIMING-UNCERTAINTY")
@pytest.mark.parametrize("uncertainty", [None, -1, float("nan"), 0.2])
def test_report_cannot_hide_missing_or_excessive_source_uncertainty(uncertainty):
    sample = {
        "sequence": 1,
        "operation": "thumb_up_recognition_observed",
        "outcome": "ok",
        "seconds": 0.4,
        "source_uncertainty_seconds": uncertainty,
    }
    observations = [
        {"owner": "synthetic", "timings": {"gesture": {"total": 1, "samples": [sample]}}}
    ]
    if uncertainty == 0.2:
        assert summarize(observations)["status"] == "blocked"
    else:
        with pytest.raises(ValueError):
            summarize(observations)
