"""Bounded transcription session counters; never retain audio or transcript text."""

import math
import time


class RecognitionUsage:
    def __init__(self, gate, model):
        self.gate, self.model = gate, model
        self.identity_origin = "local_session"
        self.started = time.monotonic()
        self.status = "closed"
        self.finalized = False
        self.seen = set()
        self.counts = {
            "attempted_pcm_bytes": 0,
            "sent_pcm_bytes": 0,
            "attempted_commits": 0,
            "sent_commits": 0,
            "completed_items": 0,
            "missing_usage_items": 0,
            "duration_usage_items": 0,
            "returned_duration_seconds": 0.0,
            "token_usage_items": 0,
            "missing_token_detail_items": 0,
            "returned_input_tokens": 0,
            "returned_output_tokens": 0,
            "returned_audio_tokens": 0,
            "returned_text_tokens": 0,
        }
        self.active_id = gate.begin_active("openai_stt", model, self.counts)
        self.identity = self.active_id

    def add(self, key, amount=1):
        if not self.finalized:
            self.counts[key] += amount

    def session(self, event):
        session = event.get("session")
        identity = session.get("id") if isinstance(session, dict) else None
        if isinstance(identity, str) and 1 <= len(identity) <= 256:
            self.identity = identity
            self.identity_origin = "provider_session"

    def completed(self, event):
        identity = (event.get("item_id"), event.get("content_index"))
        if (
            not isinstance(identity[0], str)
            or not 1 <= len(identity[0]) <= 256
            or type(identity[1]) is not int
            or not 0 <= identity[1] <= 1024
        ):
            raise ValueError("transcription_usage_identity")
        if self.finalized or identity in self.seen:
            return
        if len(self.seen) >= 10000:
            raise ValueError("transcription_usage_capacity")
        self.seen.add(identity)
        self.add("completed_items")
        usage = event.get("usage")
        if isinstance(usage, dict):
            seconds = usage.get("seconds")
            if (
                usage.get("type") == "duration"
                and type(seconds) in (int, float)
                and math.isfinite(seconds)
                and 0 <= seconds <= 86400
            ):
                self.add("duration_usage_items")
                self.add("returned_duration_seconds", seconds)
                return
            values = [usage.get(key) for key in ("input_tokens", "output_tokens", "total_tokens")]
            details = usage.get("input_token_details")
            if details is None:
                details = {}
            if (
                usage.get("type") == "tokens"
                and isinstance(details, dict)
                and all(type(v) is int and 0 <= v <= 10**9 for v in values)
                and values[0] + values[1] == values[2]
            ):
                audio, text = details.get("audio_tokens"), details.get("text_tokens")
                missing_details = audio is None or text is None
                audio = 0 if audio is None else audio
                text = 0 if text is None else text
                if (
                    all(type(v) is int and 0 <= v <= values[0] for v in (audio, text))
                    and audio + text <= values[0]
                ):
                    self.add("token_usage_items")
                    if missing_details:
                        self.add("missing_token_detail_items")
                    for key, value in zip(
                        (
                            "returned_input_tokens",
                            "returned_output_tokens",
                            "returned_audio_tokens",
                            "returned_text_tokens",
                        ),
                        (values[0], values[1], audio, text),
                        strict=True,
                    ):
                        self.add(key, value)
                    return
        self.add("missing_usage_items")

    def finish(self):
        if self.finalized:
            return
        self.finalized = True
        self.gate.end_active(self.active_id)
        self.gate.record(
            "openai_stt",
            self.identity,
            {
                **self.counts,
                "status": self.status,
                "request_id_origin": self.identity_origin,
                "elapsed_seconds": time.monotonic() - self.started,
                "sent_audio_seconds": self.counts["sent_pcm_bytes"] / 48000,
                "billing_usage_complete": False,
                "attempt_id": self.active_id,
            },
            model=self.model,
        )
