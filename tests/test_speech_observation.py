"""Controlled activity exports cannot establish acoustic echo performance."""

import pytest

from reachy_brain.core.speech_activity import SpeechActivity
from reachy_brain.evals.speech_observation import ActivitySnapshot, compare_activity

pytestmark = [pytest.mark.features("C2", "D6"), pytest.mark.scenario("SPEECH-OBSERVATION-COVERAGE")]


def snapshots(count=2):
    now = [0.0]
    activity = SpeechActivity(clock=lambda: now[0])
    start = ActivitySnapshot.model_validate(activity.snapshot())
    for n in range(count):
        now[0] += 1
        activity.record("onset" if n % 2 else "accepted_speech", n)
    now[0] += 600
    return start, ActivitySnapshot.model_validate(activity.snapshot())


def test_interval_counts_and_empty_interval_make_no_echo_claim():
    for count in (0, 2, 512):
        result = compare_activity(*snapshots(count))
        assert sum(result["counts"].values()) == count
        assert len(result["events"]) == count
        assert result["event_coverage_complete"]
        assert not result["physical_echo_validated"]


@pytest.mark.parametrize(
    "change,error",
    [
        ("owner", "owner_changed"),
        ("reverse", "interval_reversed"),
        ("drop", "events_dropped"),
        ("counts", "counts"),
        ("sequence", "sequence"),
        ("truncated", "retention"),
    ],
)
def test_incomplete_intervals_cannot_look_clean(change, error):
    start, end = snapshots(513 if change == "drop" else 2)
    if change == "owner":
        end.owner = "replacement"
    if change == "reverse":
        start.elapsed_seconds = end.elapsed_seconds + 1
    if change == "counts":
        end.counts = {"onset": 0, "uncertain_onset": 0, "accepted_speech": 2}
    if change == "sequence":
        end.samples[0].sequence = 2
    if change == "truncated":
        end.samples.pop()
    with pytest.raises(ValueError, match=error):
        compare_activity(start, end)


def test_drops_before_baseline_are_allowed_but_changed_overlap_is_not():
    now = [0.0]
    activity = SpeechActivity(clock=lambda: now[0])
    for i in range(600):
        now[0] += 1
        activity.record("onset", i)
    start = ActivitySnapshot.model_validate(activity.snapshot())
    now[0] += 1
    activity.record("uncertain_onset", 600)
    end = ActivitySnapshot.model_validate(activity.snapshot())
    result = compare_activity(start, end)
    assert result["counts"] == {"onset": 0, "uncertain_onset": 1, "accepted_speech": 0}
    end.samples[0].epoch += 1
    with pytest.raises(ValueError, match="overlapping_activity_changed"):
        compare_activity(start, end)
