"""Real loopback HTTP/WebSocket round trips, explicitly fake robot media."""

import asyncio
import io
import socket
import threading

import pytest
import uvicorn
from PIL import Image

from reachy_brain.robot.client import EdgeClient
from reachy_brain.robot.service import create_edge_app


class Media:
    input_rate = 16000

    def frame(self):
        import numpy as np

        return np.zeros((16, 16, 3), dtype=np.uint8)

    def __init__(self):
        self.closed = False

    def capture(self):
        return None

    def push_pcm(self, pcm):
        pass

    def flush(self):
        pass

    def hold(self):
        pass

    def close(self):
        self.closed = True

    def set_mode(self, mode):
        pass

    def snapshot(self):
        data = io.BytesIO()
        Image.new("RGB", (16, 16), "black").save(data, format="JPEG")
        return data.getvalue()


@pytest.mark.features("D2", "D3", "D5")
@pytest.mark.scenario("EDGE-ACTUAL-TRANSPORT")
async def test_real_edge_client_round_trip_and_cleanup():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    media = Media()
    token = "synthetic-edge-loopback-token-123"
    server = uvicorn.Server(
        uvicorn.Config(
            create_edge_app(token, lambda: media),
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
            timeout_graceful_shutdown=3,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    client = EdgeClient(f"http://127.0.0.1:{port}", token)
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started
        await client.start()
        assert client.capabilities["microphone"] == {
            "encoding": "pcm_s16le",
            "rate": 16000,
            "channels": 1,
        }
        assert client.capabilities["playback"] == {
            "encoding": "pcm_s16le",
            "rate": 24000,
            "channels": 1,
        }
        assert client.capabilities["local_stop"] is True
        assert client.capabilities["camera"]["capture_timestamps"] is False
        mode = await client.command("mode", mode="conversation")
        assert mode["mode"] == "conversation"
        authorized = await client.command(
            "authorize", epoch=1, acknowledged_stop=mode["generation"]
        )
        assert authorized["accepted"]
        stopped = await client.command("stop")
        assert stopped["generation"] > mode["generation"]
        stale = await client.command("authorize", epoch=2, acknowledged_stop=mode["generation"])
        assert not stale["accepted"]
        image, timing = await client.snapshot()
        assert Image.open(io.BytesIO(image)).size == (16, 16)
        assert not timing["capture_time_known"]
        assert client.clock.uncertainty < 1
    finally:
        await client.close()
        server.should_exit = True
        await asyncio.to_thread(thread.join, 10)
    assert not thread.is_alive() and media.closed
