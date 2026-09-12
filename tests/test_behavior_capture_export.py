"""Synthetic capture-to-desk checks; no physical duration or detector qualification."""

import hashlib
import json

import httpx
import pytest

from reachy_brain.behavior.engine import BehaviorEngine, Event
from reachy_brain.evals.behavior_capture import record
from reachy_brain.evals.behavior_capture_export import export_capture
from reachy_brain.evals.behavior_observation import BehaviorSnapshot
from reachy_brain.evals.desk_observation import DeskReview, score_desk


@pytest.mark.features("P1", "P3", "P4", "P8", "D6")
@pytest.mark.scenario("BEHAVIOR-CAPTURE-EXPORT")
@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "unfinished",
        "trailing",
        "count",
        "owner",
        "gap",
        "existing",
        "oversized",
        "version",
    ],
)
async def test_capture_export_preserves_rollover_and_actual_scorer(tmp_path, case):
    engine = BehaviorEngine()
    now = [0.0]
    calls = 0
    reviews = []

    def handle(request):
        nonlocal calls
        if calls:
            for i in range(50):
                event = str((calls - 1) * 50 + i)
                engine.offer(
                    Event(event, "camera", "wave_detected", 100 + now[0], 0.99), now=100 + now[0]
                )
                reviews.append(
                    dict(
                        event=event,
                        source="camera",
                        generation=0,
                        classification="false",
                        greeting_observed=False,
                    )
                )
        calls += 1
        return httpx.Response(200, json={"observations": engine.observations()})

    async def sleep(seconds):
        now[0] += seconds

    source, output = tmp_path / "capture.jsonl", tmp_path / "exports"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://localhost"
    ) as client:
        assert (
            await record(client, source, duration=5, interval=1, clock=lambda: now[0], sleep=sleep)
            == 6
        )
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    if case == "unfinished":
        rows.pop()
    elif case == "trailing":
        rows.append(rows[1])
    elif case == "count":
        rows[-1]["samples"] += 1
    elif case == "owner":
        rows[2]["observation"]["owner"] = "other"
    elif case == "gap":
        rows = rows[:2] + rows[-2:]
        rows[-1]["samples"] = 2
    elif case == "version":
        rows[0]["version"] = True
    source.write_bytes("".join(json.dumps(row) + "\n" for row in rows).encode())
    if case == "oversized":
        source.write_bytes(b" " * (600 * 1024 + 1))
    if case == "existing":
        output.mkdir()
        (output / "snapshots.json").write_text("preserve")
    if case != "valid":
        with pytest.raises(FileExistsError if case == "existing" else ValueError):
            export_capture(source, output)
        if case == "existing":
            assert (output / "snapshots.json").read_text() == "preserve"
        else:
            assert not output.exists()
        return
    result = export_capture(source, output)
    data = (output / "snapshots.json").read_bytes()
    assert result["snapshots_sha256"] == hashlib.sha256(data).hexdigest()
    assert result["capture_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["snapshot_count"] == 6
    assert result["decision_record_count"] == 250
    assert result["record_coverage_complete"] and not result["physical_qualification"]
    snapshots = [BehaviorSnapshot.model_validate(row) for row in json.loads(data)]
    assert snapshots[-1].dropped_samples == 50
    review = DeskReview(
        started=100,
        ended=1900,
        natural_workload_reviewed=True,
        detector_continuity_reviewed=True,
        timeline_alignment_reviewed=True,
        events=reviews,
    )
    scored = score_desk(snapshots, review)
    assert scored["event_count"] == 250
    assert scored["groups"]["wave_detected"]["false"] == 250
    assert scored["review_complete"] and not scored["physical_qualification"]
