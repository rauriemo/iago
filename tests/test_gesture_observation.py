"""Synthetic export continuity is not live gesture qualification."""

import pytest

from reachy_brain.core.gesture_activity import GestureActivity
from reachy_brain.evals.gesture_observation import GestureSnapshot, compare_gestures

pytestmark = [
    pytest.mark.features("P10", "D6"),
    pytest.mark.scenario("GESTURE-OBSERVATION-CONTINUITY"),
]


def observations(count=1):
    now = [0.0]
    activity = GestureActivity(clock=lambda: now[0])
    start = GestureSnapshot.model_validate(activity.snapshot())
    for index in range(count):
        now[0] += 1
        activity.accepted(
            dict(
                slot=str(index),
                session="s",
                question="q",
                source="camera",
                value="yes",
                gesture="thumb_up",
                turn=index,
                generation=0,
            ),
            index + 1,
        )
    now[0] += 1
    return start, GestureSnapshot.model_validate(activity.snapshot()), activity, now


def test_initial_acceptance_and_supersession_remain_separate():
    start, end, activity, now = observations()
    baseline = end
    activity.superseded("0", 2)
    now[0] += 1
    end = GestureSnapshot.model_validate(activity.snapshot())
    result = compare_gestures(start, end)
    assert result["counts"] == {"accepted": 1, "superseded": 1}
    assert [e["kind"] for e in result["events"]] == ["accepted", "superseded"]
    assert result["event_coverage_complete"] and not result["physical_gesture_validated"]
    assert not result["release_validated"]
    assert compare_gestures(baseline, end)["counts"] == {"accepted": 0, "superseded": 1}


@pytest.mark.parametrize("change", ["owner", "missing", "counts", "sequence", "time", "overlap"])
def test_incomplete_or_changed_events_reject(change):
    start, end, activity, now = observations(513 if change == "missing" else 2)
    if change == "owner":
        end.owner = "other"
    elif change == "counts":
        end.counts = {"accepted": 1, "superseded": 1}
    elif change == "sequence":
        end.samples[0].sequence = 99
    elif change == "time":
        end.samples[0].seconds = end.elapsed_seconds + 1
    elif change == "overlap":
        start = end
        now[0] += 1
        end = GestureSnapshot.model_validate(activity.snapshot())
        end.samples[0].question = "changed"
    with pytest.raises(ValueError):
        compare_gestures(start, end)


def test_incomplete_identifiers_are_rejected_instead_of_counted_as_valid():
    _, _, activity, _ = observations()
    activity.accepted({"slot": "x"}, 3)
    with pytest.raises(ValueError):
        GestureSnapshot.model_validate(activity.snapshot())


@pytest.mark.parametrize("case", ["valid", "oversized", "existing"])
def test_file_command_is_bounded_and_preserves_existing_output(tmp_path, monkeypatch, case):
    import json

    from reachy_brain.evals.gesture_observation import main

    start, end, _, _ = observations()
    first, last, output = (tmp_path / name for name in ("start.json", "end.json", "output.json"))
    first.write_text(start.model_dump_json())
    last.write_text(end.model_dump_json())
    if case == "oversized":
        first.write_bytes(b" " * (512 * 1024 + 1))
    if case == "existing":
        output.write_bytes(b"preserve user file")
    monkeypatch.setattr(
        "sys.argv",
        ["gesture-observation", "--start", str(first), "--end", str(last), "--output", str(output)],
    )
    if case == "valid":
        main()
        report = json.loads(output.read_text())
        assert report["counts"]["accepted"] == 1
        assert not report["physical_gesture_validated"]
    elif case == "oversized":
        with pytest.raises(ValueError, match="gesture_export_size_limit"):
            main()
        assert not output.exists()
    else:
        with pytest.raises(FileExistsError):
            main()
        assert output.read_bytes() == b"preserve user file"
