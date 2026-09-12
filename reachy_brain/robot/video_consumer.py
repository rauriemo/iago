"""Official camera consumer with fixed robot identity and no audio/motion callbacks.

Optional import: native desktop and robot-local media do not require aiortc.
The recovery override is tested against the locked SDK's discovery contract.
"""

import asyncio
import io
import time

import numpy as np
from PIL import Image


class VideoCamera:
    """One lazy SDK consumer shared by archive and detector snapshot requests."""

    def __init__(self, token, peer_id, *, factory=None):
        self.token, self.peer_id = token, peer_id
        self.factory = factory or create_video_consumer
        self.consumer = None
        self.lock = asyncio.Lock()
        self.encoding = asyncio.Semaphore(1)
        self.sequence = None
        self.seen_at = 0
        self.generation = 0

    async def stop(self):
        async with self.lock:
            self.generation += 1
            consumer, self.consumer = self.consumer, None
            self.sequence = None
            if consumer is not None:
                await consumer.stop()

    async def snapshot(self, *, preview=False):
        async with self.lock:
            if self.consumer is None:
                consumer = self.factory(self.token, self.peer_id)
                try:
                    await consumer.start()
                except BaseException:
                    await consumer.stop()
                    raise
                self.consumer = consumer
            consumer, generation = self.consumer, self.generation
        async with asyncio.timeout(10):
            while self.consumer is consumer and self.generation == generation:
                latest = consumer.latest_frame()
                now = time.time()
                status = consumer.status()
                if latest is not None and status.get("connected"):
                    sequence, rgb = latest
                    if sequence != self.sequence:
                        self.sequence, self.seen_at = sequence, now
                    seen_at = self.seen_at
                    if not 0 <= now - seen_at <= 1:
                        raise RuntimeError("robot_camera_stale")
                    if (
                        rgb.dtype != np.uint8
                        or rgb.ndim != 3
                        or rgb.shape[2] != 3
                        or rgb.shape[0] * rgb.shape[1] > 20_000_000
                    ):
                        raise ValueError("robot_camera_frame_limit")
                    await self.encoding.acquire()
                    if self.consumer is not consumer or self.generation != generation:
                        self.encoding.release()
                        raise RuntimeError("robot_camera_canceled")
                    worker = asyncio.create_task(asyncio.to_thread(self._encode, rgb, preview))

                    def encoded(done):
                        self.encoding.release()
                        if not done.cancelled():
                            done.exception()

                    worker.add_done_callback(encoded)
                    data = await asyncio.shield(worker)
                    if self.consumer is not consumer or self.generation != generation:
                        raise RuntimeError("robot_camera_canceled")
                    if not 0 <= time.time() - seen_at <= 1:
                        raise RuntimeError("robot_camera_stale")
                    current_status = consumer.status()
                    if not current_status.get("connected") or current_status.get(
                        "session_id"
                    ) != status.get("session_id"):
                        raise RuntimeError("robot_camera_session_changed")
                    return data, {
                        "sequence": sequence,
                        "retrieved": {"time": seen_at, "uncertainty": 1.0, "stale": False},
                        "capture_time_known": False,
                        "moving": True,
                    }
                await asyncio.sleep(0.05)
        raise RuntimeError("robot_camera_canceled")

    @staticmethod
    def _encode(rgb, preview):
        image = Image.fromarray(rgb)
        if preview:
            image.thumbnail((640, 640))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=80 if preview else 92)
        data = output.getvalue()
        if len(data) > 20 * 1024 * 1024:
            raise ValueError("robot_camera_frame_limit")
        return data


def create_video_consumer(token, peer_id):
    if not token or not isinstance(peer_id, str) or not 1 <= len(peer_id) <= 200:
        raise ValueError("robot_camera_credentials_required")
    from reachy_mini.media.central_consumer import ReachyCentralConsumer

    class PinnedCameraConsumer(ReachyCentralConsumer):
        def _auto_pick_producer(self, producers):
            # SDK 1.10 clears even a pinned target after session rejection and
            # can then select a different robot by name. Keep the configured ID.
            self._target_peer_id = (
                peer_id if any(p.get("id") == peer_id for p in producers) else None
            )

    return PinnedCameraConsumer(
        hf_token=token,
        robot_peer_id=peer_id,
        consumer_label="iago-camera",
        on_pcm=None,
        out_track=None,
        on_command_ready=None,
    )
