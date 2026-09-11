"""Synthetic signals/annotations test scoring only, never physical integration behavior."""

import hashlib

import pytest
from test_acoustic_scoring import fixture

from reachy_brain.evals.integration_acoustics import (
    ACTIVITIES,
    IntegrationCutoffTrial,
    measure_integration_cutoff,
    summarize_integration_cutoffs,
    validate_trial_identity,
)


def integration_fixture(tmp_path):
    trace = tmp_path / "synthetic-trace.txt"
    trace.write_bytes(b"synthetic held operation; not a real backend trace")
    return IntegrationCutoffTrial(
        **fixture(tmp_path).model_dump(),
        activity="mcp_read",
        activity_started_sample=8000,
        activity_released_sample=40000,
        activity_trace_file=trace.name,
        activity_trace_sha256=hashlib.sha256(trace.read_bytes()).hexdigest(),
        human_reviewed_activity=True,
        activity_alignment="synthetic sample alignment only",
    )


@pytest.mark.features("C2", "E1")
@pytest.mark.scenario("INTEGRATION-ACOUSTIC-SCORER-ACTIVITY-BINDING")
@pytest.mark.parametrize(
    "updates,error",
    [
        ({"human_reviewed_activity": False}, "reviewed_activity"),
        ({"activity_alignment": " "}, "reviewed_activity"),
        ({"activity_trace_file": "../outside"}, "outside_fixture_root"),
        ({"activity_trace_sha256": "0" * 64}, "hash_mismatch"),
        ({"activity_started_sample": 15968}, "does_not_span"),
        ({"activity_released_sample": 24000}, "does_not_span"),
        ({"activity_released_sample": 64001}, "does_not_span"),
    ],
)
def test_invalid_activity_evidence_is_rejected(tmp_path, updates, error):
    trial = integration_fixture(tmp_path).model_copy(update=updates)
    with pytest.raises(ValueError, match=error):
        measure_integration_cutoff(tmp_path, trial)


@pytest.mark.features("C2", "E1")
@pytest.mark.scenario("INTEGRATION-ACOUSTIC-SCORER-TRACE-AND-PCM")
def test_real_file_measurement_and_trace_bound(tmp_path):
    trial = integration_fixture(tmp_path)
    row = measure_integration_cutoff(tmp_path, trial)
    assert row["cutoff_upper_ms"] == 102
    assert row["activity"] == "mcp_read"
    assert row["activity_trace_sha256"] == trial.activity_trace_sha256
    for content in (b"", b"x" * (1024 * 1024 + 1)):
        (tmp_path / trial.activity_trace_file).write_bytes(content)
        with pytest.raises(ValueError, match="size_limit"):
            measure_integration_cutoff(tmp_path, trial)


@pytest.mark.features("C2", "E1")
@pytest.mark.scenario("INTEGRATION-ACOUSTIC-SCORER-DUPLICATES")
def test_duplicate_annotations_cannot_supply_additional_trials(tmp_path):
    trial = integration_fixture(tmp_path)
    validate_trial_identity([trial])
    with pytest.raises(ValueError, match="duplicate_trial_id"):
        validate_trial_identity([trial, trial])
    with pytest.raises(ValueError, match="duplicate_recorded_event"):
        validate_trial_identity([trial, trial.model_copy(update={"id": "another", "channel": 1})])
    with pytest.raises(ValueError, match="count_limit"):
        validate_trial_identity([])
    with pytest.raises(ValueError, match="count_limit"):
        validate_trial_identity([trial] * 1001)


@pytest.mark.features("C2", "E1")
@pytest.mark.scenario("INTEGRATION-ACOUSTIC-SCORER-COVERAGE")
def test_all_activities_voices_actions_and_spoken_minimum_are_required():
    rows = [
        dict(activity=a, provider=p, action=k, cutoff_upper_ms=100)
        for a in ACTIVITIES
        for p in ("openai", "elevenlabs", "fallback")
        for k in ("stop", "spoken")
    ]
    assert not summarize_integration_cutoffs(rows)["passes_target"]  # Only 15 spoken.
    rows += [
        dict(activity=a, provider="openai", action="spoken", cutoff_upper_ms=100)
        for a in ACTIVITIES
    ]
    result = summarize_integration_cutoffs(rows)
    assert result["passes_target"] and result["spoken_count"] == 20
    assert len(result["groups"]) == 30
    for index in range(30):
        reduced = rows[:index] + rows[index + 1 :]
        # Extra OpenAI spoken trials preserve their group, but removal still breaks 20 total.
        assert not summarize_integration_cutoffs(reduced)["passes_target"]
    for row in rows:
        original = row["cutoff_upper_ms"]
        row["cutoff_upper_ms"] = 150 if row["action"] == "stop" else 300
        assert not summarize_integration_cutoffs(rows)["passes_target"]
        row["cutoff_upper_ms"] = original
