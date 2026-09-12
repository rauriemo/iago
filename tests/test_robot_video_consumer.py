"""Real SDK/aiortc loopback with generated frames; no relay account or robot."""

import asyncio
import importlib.util
from unittest.mock import AsyncMock

import numpy as np
import pytest

from reachy_brain.robot.video_consumer import create_video_consumer


def require_consumer():
    if importlib.util.find_spec("aiortc") is None:
        pytest.skip("blocked: install locked robot-camera extra for official SDK WebRTC checks")


@pytest.mark.features("D2", "V1")
@pytest.mark.scenario("ROBOT-OFFICIAL-WEBRTC-VIDEO")
async def test_official_consumer_decodes_real_webrtc_without_audio_sender():
    require_consumer()
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    from av import VideoFrame

    class Video(VideoStreamTrack):
        async def recv(self):
            pts, base = await self.next_timestamp()
            pixels = np.zeros((64, 96, 3), dtype=np.uint8)
            pixels[:, :48, 0] = 240
            pixels[:, 48:, 1] = 240
            frame = VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts, frame.time_base = pts, base
            return frame

    producer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    video = Video()
    producer.addTrack(video)
    producer.addTransceiver("audio", direction="sendrecv")
    producer.createDataChannel("data")
    consumer = create_video_consumer("synthetic-unused-token", "synthetic-robot")
    # Controlled in-process signaling; actual SDP/ICE/DTLS/RTP/codec path.
    consumer._fetch_ice_servers = AsyncMock(return_value=[])
    consumer._session_id = "synthetic-session"
    consumer._post = AsyncMock(return_value={})
    try:
        async with asyncio.timeout(15):
            await producer.setLocalDescription(await producer.createOffer())
            await consumer._handle_remote_sdp(
                {"type": "offer", "sdp": producer.localDescription.sdp}
            )
            answer = consumer._post.call_args.args[0]["sdp"]
            await producer.setRemoteDescription(RTCSessionDescription(**answer))
            frame_ready = asyncio.Event()
            timer = None

            def check_frame():
                nonlocal timer
                if consumer.latest_frame() is not None:
                    frame_ready.set()
                else:
                    timer = asyncio.get_running_loop().call_later(0.02, check_frame)

            try:
                check_frame()
                await frame_ready.wait()
            finally:
                if timer is not None:
                    timer.cancel()
            sequence, rgb = consumer.latest_frame()
            assert sequence > 0 and rgb.shape == (64, 96, 3) and rgb.dtype == np.uint8
            # Codec tolerance, not lossless-image or optical qualification.
            assert rgb[:, :40, 0].mean() > 200 and rgb[:, :40, 1].mean() < 30
            assert rgb[:, 56:, 1].mean() > 200 and rgb[:, 56:, 0].mean() < 30
            assert consumer.status()["connected"]
            assert consumer._on_pcm is None and consumer._out_track is None
            assert consumer._on_command_ready is None
            assert all(sender.track is None for sender in consumer._pc.getSenders())
    finally:
        await consumer.stop()
        await producer.close()
        video.stop()
    assert not consumer._track_tasks and consumer._pc is None
    assert producer.connectionState == "closed"


@pytest.mark.features("D2", "D5")
@pytest.mark.scenario("ROBOT-CAMERA-PINNED-RECOVERY")
async def test_rejected_session_cannot_switch_to_another_robot():
    require_consumer()
    consumer = create_video_consumer("synthetic-unused-token", "chosen")
    consumer._post = AsyncMock(return_value={"type": "sessionRejected", "reason": "busy"})
    try:
        await consumer._start_session()
        assert consumer._target_peer_id is None
        consumer._auto_pick_producer([{"id": "other", "meta": {"name": "reachymini"}}])
        assert consumer._target_peer_id is None
        consumer._auto_pick_producer(
            [
                {"id": "other", "meta": {"name": "reachymini"}},
                {"id": "chosen", "meta": {"name": "changed-name"}},
            ]
        )
        assert consumer._target_peer_id == "chosen"
        consumer._post.return_value = {"type": "sessionStarted", "sessionId": "fresh"}
        await consumer._start_session()
        assert consumer._post.call_args.args[0] == {"type": "startSession", "peerId": "chosen"}
        assert consumer._session_id == "fresh"
    finally:
        await consumer.stop()
