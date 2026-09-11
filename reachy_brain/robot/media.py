"""Real SDK 1.10 LOCAL media adapter, used on the robot beside its daemon."""

import math
import threading

import numpy as np


class ReachyLocalMedia:
    def __init__(self, *, robot_factory=None, host="127.0.0.1", port=8000, motion=False):
        if robot_factory is None:
            from reachy_mini import ReachyMini

            robot_factory = ReachyMini
        self.robot = robot_factory(
            host=host,
            port=port,
            connection_mode="localhost_only",
            media_backend="local",
            spawn_daemon=False,
            timeout=3,
            automatic_body_yaw=False,
        )
        self.motion_enabled = motion
        self.motion_lock = threading.Lock()
        self.closed = False
        try:
            self.media = self.robot.media
            audio = self.media.audio
            if (
                audio is None
                or self.media.camera is None
                or not callable(getattr(audio, "clear_player", None))
            ):
                raise RuntimeError("required_local_media_unavailable")
            self.input_rate = self.media.get_input_audio_samplerate()
            self.output_rate = self.media.get_output_audio_samplerate()
            self.input_channels = self.media.get_input_channels()
            self.output_channels = self.media.get_output_channels()
            if (
                min(self.input_rate, self.output_rate, self.input_channels, self.output_channels)
                <= 0
            ):
                raise RuntimeError("invalid_media_format")
            self.media.start_recording()
            self.media.start_playing()
            audio.set_max_output_buffers(5)
        except BaseException:
            self.robot.__exit__(None, None, None)
            raise

    def capture(self):
        return self.media.get_audio_sample()

    def set_mode(self, mode):
        if mode == "idle":
            self.flush()
            self.robot.release_media()
        else:
            self.robot.acquire_media()
            if self.robot.media_released:
                raise RuntimeError("media_acquisition_failed")
            self.media = self.robot.media
            if mode == "conversation":
                self.media.start_recording()
                self.media.start_playing()
                self.media.audio.set_max_output_buffers(5)
            else:
                self.media.stop_recording()

    def frame(self):
        return self.media.get_frame()  # SDK returns source-resolution BGR uint8.

    def snapshot(self):
        return self.media.get_frame_jpeg()

    def set_camera(self, enabled):
        if enabled:
            self.media.camera.open()
        else:
            self.media.camera.close()

    def push_pcm(self, pcm):
        from scipy.signal import resample_poly

        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        if self.output_rate != 24000:
            factor = math.gcd(self.output_rate, 24000)
            samples = resample_poly(samples, self.output_rate // factor, 24000 // factor).astype(
                np.float32
            )
        self.media.push_audio_sample(samples)

    def flush(self):
        if getattr(self.robot, "media_released", False):
            return
        # SDK stop_playing stops its shared pipeline. Flush keeps capture available.
        self.media.audio.clear_player()

    def hold(self):
        self.robot.cancel_move()
        if self.motion_enabled:
            with self.motion_lock:
                pose = self.robot.get_current_head_pose()
                self.robot.set_target(head=pose)

    def target(self, head, antennas=None):
        if not self.motion_enabled:
            return False
        pose = np.asarray(head, dtype=np.float64)
        if (
            pose.shape != (4, 4)
            or not np.isfinite(pose).all()
            or np.linalg.norm(pose[:3, 3]) > 0.03
        ):
            raise ValueError("motion_limit")
        rotation = pose[:3, :3]
        if (
            not np.allclose(pose[3], [0, 0, 0, 1])
            or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5)
            or abs(np.linalg.det(rotation) - 1) > 1e-5
        ):
            raise ValueError("invalid_pose")
        if math.acos(float(np.clip((np.trace(rotation) - 1) / 2, -1, 1))) > 0.25:
            raise ValueError("rotation_limit")
        if antennas is not None and (
            len(antennas) != 2 or any(not math.isfinite(a) or abs(a) > 0.4 for a in antennas)
        ):
            raise ValueError("antenna_limit")
        with self.motion_lock:
            self.robot.set_target(head=pose, antennas=antennas)
        return True

    def pose(self):
        return self.robot.get_current_head_pose()

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.flush()
                self.hold()
                if not getattr(self.robot, "media_released", False):
                    self.media.stop_recording()
                    self.media.stop_playing()
            finally:
                self.robot.__exit__(None, None, None)
