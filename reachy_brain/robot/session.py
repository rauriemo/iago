"""Persistent backend conversation ownership for assisted and standalone robot profiles."""

import asyncio
import base64
import contextlib
import time
import uuid
from collections import deque

import numpy as np

from reachy_brain.providers.live import Transcription

from .client import EdgeClient
from .local_client import LocalClient
from .video_consumer import VideoCamera


class RobotSession:
    def __init__(self, settings, gate, core, notify, *, client_factory=None, on_preview=None):
        self.settings, self.gate, self.core, self.notify = settings, gate, core, notify
        client_factory = client_factory or (
            LocalClient if settings.deployment_mode == "reachy_local" else EdgeClient
        )
        self.client_factory = client_factory
        self.closed = False
        self.reconnect_task = None
        self.auto_reconnect_used = False
        self.recovery_delays = (0.5, 1, 2)
        self.edge = client_factory(
            settings.iago_edge_url,
            settings.iago_edge_token.get_secret_value(),
            ca_file=settings.iago_edge_ca_file,
            on_event=self.event,
        )
        self.stt = None
        self.tasks = set()
        self.marks = []
        self.last_sequence = -1
        self.last_played = (-1, -1)
        self.source = None
        self.camera_enabled = True
        self.motion_enabled = False
        self.camera_tasks = set()
        self.capture_wake = asyncio.Event()
        self.error = None
        self.speech_event_ids = deque(maxlen=128)
        self.input_generation = 0
        self.muted = False
        self.audio_settings = {"muted": False, "patient": False, "volume": 0.8}
        self.on_preview = on_preview
        self.video = (
            VideoCamera(
                settings.robot_camera_hf_token.get_secret_value(), settings.robot_camera_peer_id
            )
            if settings.robot_camera_transport == "webrtc"
            else None
        )

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def start(self):
        await self.edge.start()

    def reconnect(self, *, automatic=False):
        if (
            self.closed
            or (automatic and self.auto_reconnect_used)
            or (self.reconnect_task and not self.reconnect_task.done())
        ):
            return False
        self.auto_reconnect_used = True
        self.reconnect_task = asyncio.create_task(self._recover())
        return True

    async def _recover(self):
        await self._cancel_tasks()
        if self.stt:
            await self.stt.close()
            self.stt = None
        self.edge.on_event = None
        await self.edge.close()
        self.edge.error = "reconnecting"
        await self.core.set_mode("idle")
        self.marks.clear()
        self.speech_event_ids.clear()
        self.last_sequence, self.last_played = -1, (-1, -1)
        self.source = None
        self.motion_enabled = False
        await self.notify({"type": "robot_connection", "status": "reconnecting"})
        for delay in self.recovery_delays:
            await asyncio.sleep(delay)
            if self.closed:
                return
            candidate = self.client_factory(
                self.settings.iago_edge_url,
                self.settings.iago_edge_token.get_secret_value(),
                ca_file=self.settings.iago_edge_ca_file,
                on_event=self.event,
            )
            self.edge = candidate
            try:
                await candidate.start()
                await candidate.command("mode", mode="idle")
                await candidate.command("motion_enabled", enabled=False)
                await candidate.command("camera", enabled=self.camera_enabled)
                await self.configure_audio(**self.audio_settings)
                self.core.connection = uuid.uuid4().hex
                self.core.stop_generation = candidate.stop_generation
                self.error = None
                await self.notify({"type": "robot_motion", "enabled": False})
                await self.notify({"type": "robot_connection", "status": "connected_idle"})
                return
            except asyncio.CancelledError:
                candidate.error = "reconnect_canceled"
                candidate.on_event = None
                await candidate.close()
                raise
            except Exception:
                candidate.on_event = None
                await candidate.close()
                candidate.error = "reconnect_attempt_failed"
        self.error = "reconnect_exhausted"
        await self.notify({"type": "robot_connection", "status": self.error})

    async def configure_audio(self, muted, patient, volume):
        result = await self.edge.command(
            "audio_settings", muted=muted, patient=patient, volume=volume
        )
        self.input_generation = result["input_generation"]
        self.muted = result["muted"]
        self.audio_settings = {key: result[key] for key in ("muted", "patient", "volume")}
        if self.muted:
            self.core.user_speaking = False
        return result

    async def set_motion(self, enabled):
        result = await self.edge.command("motion_enabled", enabled=enabled)
        self.motion_enabled = result["enabled"]
        await self.notify({"type": "robot_motion", "enabled": self.motion_enabled})

    async def cue(self, kind, epoch):
        if not self.motion_enabled or self.core.mode == "idle" or self.core.epoch != epoch:
            return
        try:
            await self.edge.command(
                "motion_cue", cue=kind, acknowledged_stop=self.edge.stop_generation
            )
        except Exception as exc:
            await self.notify(
                {"type": "error", "message": "Motion cue unavailable (" + type(exc).__name__ + ")."}
            )

    async def finish_turn(self):
        if not self.stt or self.core.mode != "conversation":
            raise RuntimeError("recognition_not_active")
        result = await self.edge.command("finish_turn")
        if not result["accepted"]:
            raise RuntimeError("microphone_not_active")

    async def send(self, message):
        kind = message["type"]
        if kind == "stop":
            self.marks.clear()
            if not message.get("sink_already_stopped") and not self.edge.error:
                result = await self.edge.command("stop")
                self.core.stop_generation = result["generation"]
        elif kind == "authorize":
            self.last_sequence = -1
            self.last_played = (-1, -1)
            result = await self.edge.command(
                "authorize", epoch=message["epoch"], acknowledged_stop=self.edge.stop_generation
            )
            if not result["accepted"]:
                raise RuntimeError("edge_authorization_rejected")
        elif kind == "audio":
            result = await self.edge.command(
                "audio", epoch=message["epoch"], sequence=message["sequence"], pcm=message["pcm"]
            )
            if not result["accepted"]:
                raise RuntimeError("edge_audio_rejected")
            self.last_sequence = message["sequence"]
        elif kind == "segment_end":
            mark = (message["epoch"], self.last_sequence, message["segment"])
            if self.last_played[0] == mark[0] and self.last_played[1] >= mark[1]:
                self.spawn(self.heard_later(mark))
            else:
                self.marks.append(mark)
        if kind == "authorize":
            self.spawn(self.cue("speaking", self.core.epoch))
        elif kind == "state" and message.get("mode") == "conversation":
            self.spawn(self.cue("listening", self.core.epoch))
        if kind not in {"audio", "authorize", "segment_end"}:
            await self.notify(message)

    async def event(self, event, *, from_audio=False):
        kind = event["type"]
        if kind == "stop":
            if event["generation"] > self.core.stop_generation:
                await self.core.stop(generation=event["generation"], sink_already_stopped=True)
        elif kind == "played":
            self.last_played = (event["epoch"], event["sequence"])
            if self.core.speech:
                self.core.speech.acknowledge(event["epoch"], event["sequence"])
            for mark in list(self.marks):
                if mark[0] == event["epoch"] and mark[1] <= event["sequence"]:
                    self.marks.remove(mark)
                    self.spawn(self.heard_later(mark))
        elif kind == "speech_start":
            key = (kind, event.get("sequence"))
            if key in self.speech_event_ids:
                return
            self.speech_event_ids.append(key)
            captured = event.get("captured")
            mapped = (
                self.edge.clock.local(captured, now=time.time()) if captured is not None else None
            )
            if not mapped or mapped["stale"] or mapped["uncertainty"] > 0.1:
                await self.core.uncertain_speech_onset()
            else:
                await self.core.speech_onset(mapped["time"], sink_already_stopped=True)
        elif kind == "speech_end":
            # Only the marker on the ordered microphone channel may commit its audio.
            key = (kind, event.get("sequence"))
            if from_audio and self.stt and key not in self.speech_event_ids:
                self.speech_event_ids.append(key)
                captured = event.get("captured")
                mapped = (
                    self.edge.clock.local(captured, now=time.time())
                    if captured is not None
                    else None
                )
                end = (
                    mapped["time"]
                    if mapped and not mapped["stale"] and mapped["uncertainty"] <= 0.1
                    else None
                )
                await self.core.commit_recognition(self.stt, capture_end=end)
        elif kind == "disconnected" and not self.closed:
            await self.fail("edge_disconnected")
            self.reconnect(automatic=True)

    async def heard_later(self, mark):
        await asyncio.sleep(self.settings.robot_output_latency_allowance)
        if self.core.valid(mark[0]):
            await self.core.heard(mark[0], mark[2])

    async def set_mode(self, mode):
        if mode not in {"idle", "aware", "conversation"}:
            raise ValueError("invalid_mode")
        if self.reconnect_task and not self.reconnect_task.done():
            if mode != "idle":
                raise RuntimeError("edge_reconnecting")
            self.reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.reconnect_task
            await self.core.set_mode("idle")
            return
        try:
            # Stop the old answer before potentially slow provider setup. Release edge
            # capture during transitions so failure cannot leave an old mode recording.
            await self.core.stop(sink_already_stopped=bool(self.edge.error))
            await self.edge.command("mode", mode="idle")
            await self._set_mode(mode)
            if mode != "idle":
                self.auto_reconnect_used = False
            self.error = None
        except BaseException:
            await self._cancel_tasks()
            if self.stt:
                with contextlib.suppress(Exception):
                    await self.stt.close()
                self.stt = None
            with contextlib.suppress(Exception):
                await self.edge.command("mode", mode="idle")
            self.core.mode = "idle"
            self.core.thumbs.invalidate("mode_transition_failed")
            self.core.visual.clear(disable=True)
            self.core.session_ended = self.core.session_ended or time.time()
            self.core.record_session()
            self.error = "mode_transition_failed"
            with contextlib.suppress(Exception):
                await self.notify({"type": "state", "mode": "idle", "voice_reason": self.error})
            raise

    async def _cancel_tasks(self, *, stop_video=True):
        current = asyncio.current_task()
        for task in list(self.tasks):
            if task is not current:
                task.cancel()
        await asyncio.gather(*(t for t in self.tasks if t is not current), return_exceptions=True)
        if self.video and stop_video:
            await self.video.stop()

    async def _set_mode(self, mode):
        await self._cancel_tasks(stop_video=mode == "idle")
        if self.stt:
            await self.stt.close()
            self.stt = None
        if mode == "conversation":
            await self.core.select_voice()
            self.stt = Transcription(self.settings, self.gate)
            await self.stt.start()
        result = await self.edge.command("mode", mode=mode)
        self.core.stop_generation = result["generation"]
        await self.core.set_mode(mode)
        self.speech_event_ids.clear()
        if mode == "conversation":
            self.spawn(self.microphone())
            self.spawn(self.transcripts())
        if mode != "idle" and self.camera_enabled:
            if not self.source or not self.source.enabled:
                self.source = self.core.visual.source(
                    self.core.connection, "camera", "Reachy camera Â· capture timing unqualified"
                )
            self.core.visual.capture_hooks[self.source.id] = self.request_capture
            self.core.selected_source = self.source.id
            self.spawn_camera(self.camera())
            if self.settings.perception_enabled and self.on_preview:
                self.spawn_camera(self.previews())

    def spawn_camera(self, coro):
        task = self.spawn(coro)
        self.camera_tasks.add(task)
        task.add_done_callback(self.camera_tasks.discard)

    async def set_camera(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("invalid_camera_setting")
        if enabled and self.camera_enabled:
            return
        if not enabled:
            self.camera_enabled = False
            for task in list(self.camera_tasks):
                task.cancel()
            await asyncio.gather(*self.camera_tasks, return_exceptions=True)
            if self.video:
                await self.video.stop()
            if self.source:
                await self.core.clear_evidence(self.source.id, disable=True)
        await self.edge.command("camera", enabled=enabled)
        self.camera_enabled = enabled
        if enabled and self.core.mode != "idle":
            self.source = self.core.visual.source(
                self.core.connection, "camera", "Reachy camera - capture timing unqualified"
            )
            self.core.visual.capture_hooks[self.source.id] = self.request_capture
            self.core.selected_source = self.source.id
            self.spawn_camera(self.camera())
            if self.settings.perception_enabled and self.on_preview:
                self.spawn_camera(self.previews())
        await self.notify({"type": "robot_camera", "enabled": enabled})

    async def fail(self, reason):
        self.error = reason
        await self.core.stop(sink_already_stopped=bool(self.edge.error))
        if self.edge.error:
            self.core.mode = "idle"
            self.core.session_ended = self.core.session_ended or time.time()
            self.core.record_session()
            for task in list(self.tasks):
                if task is not asyncio.current_task():
                    task.cancel()
            if self.stt:
                await self.stt.close()
                self.stt = None
            await self.core.emit(
                "state",
                mode="idle",
                voice=self.core.voice_provider,
                voice_reason="edge_disconnected",
            )
        else:
            await self.set_mode("aware")
        await self.notify({"type": "error", "message": "Robot session stopped: " + reason})

    async def transcripts(self):
        try:
            async for event in self.stt.events():
                await self.core.transcription_event(event)
            await self.fail("transcription_stream_ended")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.fail(type(exc).__name__)

    async def microphone(self):
        try:
            async for packet in self.edge.microphone():
                if self.muted or packet.get("input_generation", 0) != self.input_generation:
                    continue
                if packet["gap"]:
                    raise RuntimeError("microphone_sequence_gap")
                samples = np.frombuffer(base64.b64decode(packet["pcm"], validate=True), dtype="<i2")
                if packet["rate"] != 24000:
                    count = round(len(samples) * 24000 / packet["rate"])
                    samples = np.interp(
                        np.arange(count) * packet["rate"] / 24000, np.arange(len(samples)), samples
                    ).astype("<i2")
                pcm = samples.tobytes()
                for at in range(0, len(pcm), 9600):
                    await self.stt.append(pcm[at : at + 9600])
                for event in packet.get("speech_events", []):
                    await self.event(event, from_audio=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.fail(type(exc).__name__)

    def request_capture(self):
        """Wake the sole archive owner; repeated requests coalesce into one wakeup."""
        if (
            self.closed
            or self.core.mode == "idle"
            or not self.camera_enabled
            or self.source is None
            or not self.source.enabled
        ):
            return False
        self.capture_wake.set()
        return True

    async def camera(self):
        try:
            while self.core.mode != "idle" and self.source.enabled:
                self.capture_wake.clear()
                source, generation = self.source, self.source.generation
                image, timing = await (getattr(self, "video", None) or self.edge).snapshot()
                if (
                    self.core.mode == "idle"
                    or self.source is not source
                    or not source.enabled
                    or source.generation != generation
                ):
                    continue
                sequence = timing.get("sequence")
                if type(sequence) is not int or not 0 <= sequence <= 9007199254740991:
                    raise ValueError("invalid_robot_frame_sequence")
                if sequence <= source.last_frame_sequence:
                    try:
                        await asyncio.wait_for(self.capture_wake.wait(), 1)
                    except TimeoutError:
                        pass
                    continue
                prepared = await asyncio.to_thread(self.core.visual.prepare, image)
                if (
                    self.core.mode == "idle"
                    or self.source is not source
                    or not source.enabled
                    or source.generation != generation
                ):
                    continue
                frame = self.core.visual.add(
                    source.id, generation, time.time(), prepared, sequence=sequence
                )
                frame.capture_time_known = False
                frame.timing_note = (
                    "WebRTC frame observed by backend; physical capture timing unqualified"
                    if getattr(self, "video", None)
                    else "SDK retrieval timestamp only; physical capture timing unqualified"
                )
                try:
                    await asyncio.wait_for(self.capture_wake.wait(), 1)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if getattr(self, "video", None):
                await self.video.stop()
                self.camera_enabled = False
                if self.source and self.source.enabled:
                    await self.core.clear_evidence(self.source.id, disable=True)
                with contextlib.suppress(Exception):
                    await self.edge.command("camera", enabled=False)
                await self.notify({"type": "robot_camera", "enabled": False})
            await self.notify(
                {"type": "error", "message": "Robot camera unavailable: " + type(exc).__name__}
            )

    async def previews(self):
        """Small detector frames have an independent cadence from the visual archive."""
        previous, identity = -1, None
        try:
            while self.core.mode != "idle" and self.source.enabled:
                source, generation = self.source, self.source.generation
                image, timing = await (getattr(self, "video", None) or self.edge).snapshot(
                    preview=True
                )
                mapped = timing["retrieved"]
                sequence = timing.get("sequence")
                if type(sequence) is not int or not 0 <= sequence <= 9007199254740991:
                    raise ValueError("invalid_robot_preview_sequence")
                if identity != (source.id, generation):
                    identity, previous = (source.id, generation), -1
                if (
                    self.core.mode != "idle"
                    and self.source is source
                    and source.enabled
                    and source.generation == generation
                    and sequence > previous
                    and not mapped["stale"]
                ):
                    previous = sequence
                    # The SDK exposes retrieval time, not capture PTS. Only an explicitly
                    # measured capture-delay bound can qualify temporal gestures.
                    bound = self.settings.robot_camera_timing_uncertainty
                    uncertainty = 1.0 if bound is None else bound + mapped["uncertainty"]
                    await self.on_preview(
                        source,
                        generation,
                        mapped["time"],
                        image,
                        uncertainty,
                        timing.get("moving", True),
                        sequence,
                    )
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.notify(
                {"type": "error", "message": "Robot perception unavailable: " + type(exc).__name__}
            )

    async def close(self):
        self.closed = True
        if self.reconnect_task and not self.reconnect_task.done():
            self.reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.reconnect_task
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.video:
            await self.video.stop()
        if self.stt:
            await self.stt.close()
        with contextlib.suppress(Exception):
            await self.edge.command("mode", mode="idle")
        await self.edge.close()
