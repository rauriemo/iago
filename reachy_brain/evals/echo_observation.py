"""Reviewed independent echo recordings; backend zero counts alone cannot pass."""

import hashlib
import tempfile
import wave
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from pydantic import Field

from .screen_grounding import StrictRecord, digest
from .speech_observation import compare_activity


class PlaybackInterval(StrictRecord):
    start_sample: int = Field(ge=0)
    end_sample: int = Field(ge=1)
    provider: Literal["openai", "elevenlabs", "fallback"]


class EchoCapture(StrictRecord):
    version: Literal[1]
    fixture_kind: Literal["physical-pc-independent-recording"]
    file: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    start_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    end_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    application_revision: str = Field(min_length=1, max_length=128)
    devices: str = Field(min_length=1, max_length=2000)
    channel: int = Field(ge=0, le=7)
    microphone_channel: int = Field(ge=0, le=7)
    voice_ids: dict[
        Literal["openai", "elevenlabs", "fallback"],
        Annotated[str, Field(min_length=1, max_length=128)],
    ] = Field(min_length=3, max_length=3)
    # Sample zero mapped to the backend owner's elapsed clock by independent review.
    recording_start_seconds: float = Field(ge=0)
    alignment_uncertainty_seconds: float = Field(ge=0, le=0.1)
    playback: list[PlaybackInterval] = Field(min_length=3, max_length=1000)


class RobotEchoCapture(EchoCapture):
    fixture_kind: Literal["physical-robot-independent-recording"]
    profile: Literal["reachy_pc", "reachy_local"]
    robot_identity: str = Field(min_length=1, max_length=256)
    daemon_version: str = Field(min_length=1, max_length=128)
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EchoReview(StrictRecord):
    capture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1, max_length=128)
    independent_human_review: bool
    recording_origin_verified: bool
    microphone_continuity_verified: bool
    playback_intervals_verified: bool
    clock_alignment_verified: bool
    continuity_evidence: str = Field(min_length=1, max_length=4000)
    # Every backend event is classified, including onsets with no saved transcript.
    events: dict[str, Literal["user", "assistant_echo", "other", "unknown"]] = Field(max_length=512)


def score_echo(root: Path, capture: EchoCapture, review: EchoReview, start, end):
    activity = compare_activity(start, end)
    if capture.start_sha256 != digest(start) or capture.end_sha256 != digest(end):
        raise ValueError("echo_activity_binding_mismatch")
    if review.capture_sha256 != digest(capture):
        raise ValueError("echo_review_binding_mismatch")
    if not all(
        (
            review.reviewer.strip(),
            review.continuity_evidence.strip(),
            review.independent_human_review,
            review.recording_origin_verified,
            review.microphone_continuity_verified,
            review.playback_intervals_verified,
            review.clock_alignment_verified,
        )
    ):
        raise ValueError("independent_echo_review_required")
    events = activity["events"]
    if set(review.events) != {str(e["sequence"]) for e in events}:
        raise ValueError("echo_event_review_incomplete")
    root = root.resolve()
    path = (root / capture.file).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("echo_recording_outside_root")
    # Hash and measure the same private snapshot, without allocating the full
    # observation or trusting that the original remains unchanged after hashing.
    with path.open("rb") as stream, tempfile.TemporaryFile() as snapshot:
        hashed = hashlib.sha256()
        size = 0
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > 512 * 1024 * 1024:
                raise ValueError("echo_recording_size_limit")
            hashed.update(chunk)
            snapshot.write(chunk)
        if hashed.hexdigest() != capture.sha256:
            raise ValueError("echo_recording_hash_mismatch")
        snapshot.seek(0)
        with wave.open(snapshot, "rb") as wav:
            rate, channels, count = wav.getframerate(), wav.getnchannels(), wav.getnframes()
            if (
                wav.getsampwidth() != 2
                or wav.getcomptype() != "NONE"
                or not 8000 <= rate <= 96000
                or not 2 <= channels <= 8
                or capture.channel >= channels
                or capture.microphone_channel >= channels
                or capture.microphone_channel == capture.channel
                or not 600 <= count / rate <= 7200
            ):
                raise ValueError("unsupported_echo_recording")
            wav.setpos(count - 1)
            if len(wav.readframes(1)) != channels * 2:
                raise ValueError("truncated_echo_recording")
            begin = capture.recording_start_seconds
            uncertainty = capture.alignment_uncertainty_seconds
            if (
                begin + uncertainty > start.elapsed_seconds
                or begin + count / rate - uncertainty < end.elapsed_seconds
            ):
                raise ValueError("echo_recording_does_not_cover_activity")
            previous = 0
            duration = 0.0
            providers = set()
            for span in capture.playback:
                if not previous <= span.start_sample < span.end_sample <= count:
                    raise ValueError("echo_playback_overlap_or_bounds")
                previous = span.end_sample
                if (
                    begin + span.start_sample / rate - uncertainty < start.elapsed_seconds
                    or begin + span.end_sample / rate + uncertainty > end.elapsed_seconds
                ):
                    raise ValueError("echo_playback_outside_observation")
                wav.setpos(span.start_sample)
                remaining = span.end_sample - span.start_sample
                nonzero = False
                while remaining:
                    frames = min(remaining, rate)
                    raw = wav.readframes(frames)
                    if len(raw) != frames * channels * 2:
                        raise ValueError("truncated_echo_recording")
                    samples = np.frombuffer(raw, dtype="<i2").reshape(-1, channels)
                    nonzero |= bool(np.any(samples[:, capture.channel]))
                    remaining -= frames
                if not nonzero:
                    raise ValueError("silent_echo_playback_interval")
                duration += (span.end_sample - span.start_sample) / rate
                providers.add(span.provider)
    if duration < 600 or providers != {"openai", "elevenlabs", "fallback"}:
        raise ValueError("insufficient_echo_playback_coverage")
    echoed = [e for e in events if review.events[str(e["sequence"])] == "assistant_echo"]
    uncertain = [e for e in events if review.events[str(e["sequence"])] == "unknown"]
    self_turns = [e for e in echoed if e["kind"] == "accepted_speech"]
    return {
        "capture_sha256": digest(capture),
        "review_sha256": digest(review),
        "recording_sha256": capture.sha256,
        "devices": capture.devices,
        "application_revision": capture.application_revision,
        "voice_ids": capture.voice_ids,
        "reviewed_playback_seconds": duration,
        "activity": activity,
        "assistant_echo_notifications": len(echoed),
        "self_triggered_turns": len(self_turns),
        "unclassified_notifications": len(uncertain),
        "passes_reviewed_echo_check": not self_turns and not uncertain,
        "fixture_kind": capture.fixture_kind,
        "scope": "Recorded observation with independent human declarations; does not authenticate the reviewer or recording origin, qualify interruption cutoff/opening words, or establish general echo performance.",
    }
