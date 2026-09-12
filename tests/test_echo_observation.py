"""Synthetic PCM and review declarations test the scorer, never physical echo."""

import hashlib
import wave

import pytest
from pydantic import ValidationError

from reachy_brain.core.speech_activity import SpeechActivity
from reachy_brain.evals.echo_observation import (
    EchoCapture,
    EchoReview,
    RobotEchoCapture,
    score_echo,
)
from reachy_brain.evals.screen_grounding import digest
from reachy_brain.evals.speech_observation import ActivitySnapshot

pytestmark = [pytest.mark.features("C2", "D6"), pytest.mark.scenario("ECHO-RECORDING-REVIEW")]


@pytest.fixture
def bundle(tmp_path):
    now = [1.0]
    activity = SpeechActivity(clock=lambda: now[0])
    start = ActivitySnapshot.model_validate(activity.snapshot())
    now[0] = 101.0
    activity.record("accepted_speech", 1)
    now[0] = 601.0
    end = ActivitySnapshot.model_validate(activity.snapshot())
    path = tmp_path / "synthetic.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setparams((2, 2, 8000, 0, "NONE", "not compressed"))
        for _ in range(600):
            wav.writeframes(b"\x01\x00\x01\x00" * 8000)
    capture = EchoCapture(
        version=1,
        fixture_kind="physical-pc-independent-recording",
        file=path.name,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        start_sha256=digest(start),
        end_sha256=digest(end),
        application_revision="synthetic",
        devices="synthetic; no physical claim",
        channel=0,
        microphone_channel=1,
        voice_ids={p: "synthetic" for p in ("openai", "elevenlabs", "fallback")},
        recording_start_seconds=0.0,
        alignment_uncertainty_seconds=0.0,
        playback=[
            {
                "start_sample": i * 200 * 8000,
                "end_sample": (i + 1) * 200 * 8000,
                "provider": provider,
            }
            for i, provider in enumerate(("openai", "elevenlabs", "fallback"))
        ],
    )
    review = EchoReview(
        capture_sha256=digest(capture),
        reviewer="synthetic reviewer declaration",
        independent_human_review=True,
        recording_origin_verified=True,
        microphone_continuity_verified=True,
        playback_intervals_verified=True,
        clock_alignment_verified=True,
        continuity_evidence="synthetic test only",
        events={"1": "user"},
    )
    return tmp_path, capture, review, start, end


def test_complete_review_counts_self_turns_and_uncertainty(bundle):
    root, capture, review, start, end = bundle
    result = score_echo(*bundle)
    assert result["reviewed_playback_seconds"] == 600
    assert result["passes_reviewed_echo_check"]
    review.events["1"] = "assistant_echo"
    result = score_echo(root, capture, review, start, end)
    assert result["self_triggered_turns"] == 1
    assert not result["passes_reviewed_echo_check"]
    review.events["1"] = "unknown"
    assert not score_echo(root, capture, review, start, end)["passes_reviewed_echo_check"]


@pytest.mark.scenario("ECHO-HASHED-RECORDING-SNAPSHOT")
def test_pcm_measurement_uses_the_hashed_snapshot(bundle, monkeypatch):
    root, capture, review, start, end = bundle
    original = wave.open

    def replace_after_hash(source, mode):
        (root / capture.file).write_bytes(b"changed after hash verification")
        return original(source, mode)

    monkeypatch.setattr(wave, "open", replace_after_hash)
    result = score_echo(root, capture, review, start, end)
    assert result["recording_sha256"] == capture.sha256
    assert result["reviewed_playback_seconds"] == 600
    assert result["passes_reviewed_echo_check"]


@pytest.mark.features("C2", "D2", "D3", "D6")
@pytest.mark.scenario("ROBOT-ECHO-PROFILE-REVIEW-BINDING")
def test_robot_capture_requires_its_own_profile_bound_review(bundle):
    root, capture, review, start, end = bundle
    with pytest.raises(ValidationError):
        RobotEchoCapture.model_validate(capture.model_dump())
    data = dict(
        capture.model_dump(),
        fixture_kind="physical-robot-independent-recording",
        profile="reachy_pc",
        robot_identity="synthetic",
        daemon_version="synthetic",
        configuration_sha256="0" * 64,
    )
    robot = RobotEchoCapture.model_validate(data)
    with pytest.raises(ValueError, match="echo_review_binding_mismatch"):
        score_echo(root, robot, review, start, end)
    review.capture_sha256 = digest(robot)
    assert score_echo(root, robot, review, start, end)["passes_reviewed_echo_check"]
    robot.profile = "reachy_local"
    with pytest.raises(ValueError, match="echo_review_binding_mismatch"):
        score_echo(root, robot, review, start, end)


@pytest.mark.parametrize(
    "change,error",
    [
        ("microphone", "independent_echo_review"),
        ("events", "event_review_incomplete"),
        ("binding", "activity_binding"),
        ("overlap", "overlap_or_bounds"),
        ("short", "insufficient_echo_playback"),
        ("hash", "recording_hash"),
        ("outside", "outside_root"),
        ("clock", "does_not_cover_activity"),
        ("same_channel", "unsupported_echo_recording"),
    ],
)
def test_incomplete_or_changed_observations_rejected(bundle, change, error):
    root, capture, review, start, end = bundle
    if change == "microphone":
        review.microphone_continuity_verified = False
    if change == "events":
        review.events = {}
    if change == "binding":
        capture.start_sha256 = "0" * 64
    if change == "overlap":
        capture.playback[1].start_sample -= 1
    if change == "short":
        capture.playback[-1].end_sample -= 8000
    if change == "hash":
        capture.sha256 = "0" * 64
    if change == "outside":
        capture.file = "../outside.wav"
    if change == "clock":
        capture.recording_start_seconds = 1.0
    if change == "same_channel":
        capture.microphone_channel = capture.channel
    review.capture_sha256 = digest(capture)
    with pytest.raises(ValueError, match=error):
        score_echo(root, capture, review, start, end)


@pytest.mark.parametrize("condition", ["complete", "echo", "missing_recording"])
def test_live_entrypoint_with_synthetic_bundle(bundle, monkeypatch, condition):
    from test_physical_echo import test_reviewed_ten_minute_echo_observation as check

    root, capture, review, start, end = bundle
    if condition == "echo":
        review.events["1"] = "assistant_echo"
    for name, value in zip(("capture", "review", "start", "end"), bundle[1:], strict=True):
        (root / (name + ".json")).write_text(value.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("IAGO_PC_ECHO_FIXTURES", str(root))
    properties = {}
    if condition == "missing_recording":
        (root / capture.file).unlink()
        with pytest.raises(pytest.skip.Exception, match="recording unavailable"):
            check(properties.__setitem__)
    elif condition == "echo":
        with pytest.raises(AssertionError, match="Self-triggered"):
            check(properties.__setitem__)
        assert properties["measurements"]["self_triggered_turns"] == 1
    else:
        check(properties.__setitem__)
        assert properties["measurements"]["reviewed_playback_seconds"] == 600
