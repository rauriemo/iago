"""Controlled native media release; no physical robot claim."""

import asyncio
import threading
from types import SimpleNamespace

import pytest
from anyio import CancelScope
from fastapi import WebSocketDisconnect

from reachy_brain.robot.service import create_edge_app


@pytest.mark.features("D2", "D3", "D5", "C2")
@pytest.mark.scenario("EDGE-CANCELED-RELEASE-OWNERSHIP")
@pytest.mark.parametrize("cancellation", ["native", "scope"])
@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("disconnect_at", ["read", "ready"])
async def test_cancel_during_release_keeps_owner_until_media_idle(
    cancellation, failure, disconnect_at
):
    entered, release = threading.Event(), threading.Event()
    modes = []
    token = "synthetic-edge-token-at-least-24"
    app = create_edge_app(token)
    state = app.state.edge

    def set_mode(mode):
        entered.set()
        assert release.wait(5), "test must release native worker"
        if failure:
            raise RuntimeError("synthetic media failure")
        modes.append(mode)

    runtime = SimpleNamespace(
        supervisor=SimpleNamespace(connect=lambda *a, **k: None, stop=lambda: None),
        media=SimpleNamespace(set_mode=set_mode, input_rate=16000),
        camera=SimpleNamespace(set_enabled=lambda enabled: None),
        capture_enabled=True,
        finish_requested=True,
        error=None,
    )
    state["runtime"] = runtime

    class Socket:
        calls = 0
        closed = False

        async def close(self, **kwargs):
            self.closed = True

        async def accept(self):
            pass

        async def receive_json(self):
            self.calls += 1
            if self.calls == 1:
                return {
                    "token": token,
                    "session": "session",
                    "connection": "connection",
                    "protocol_version": 1,
                }
            raise WebSocketDisconnect()

        async def send_json(self, message):
            if disconnect_at == "ready":
                raise WebSocketDisconnect()

    endpoint = next(
        route.endpoint for route in app.routes if getattr(route, "path", None) == "/control"
    )
    scope = CancelScope()

    async def run():
        with scope:
            await endpoint(Socket())

    task = asyncio.create_task(run())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        cancel = task.cancel if cancellation == "native" else scope.cancel
        cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert state["owner"] == ("session", "connection")
        assert not task.done()
        assert not runtime.capture_enabled and not runtime.finish_requested
        cancel()  # Repeated cancellation must not release ownership either.
        await asyncio.sleep(0)
        assert state["owner"] == ("session", "connection")
        replacement = Socket()
        await endpoint(replacement)
        assert replacement.closed
    finally:
        release.set()
        if failure:
            with pytest.raises(RuntimeError, match="synthetic media failure"):
                await task
        elif cancellation == "native":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task
    if failure:
        assert modes == [] and state["owner"] == ("session", "connection")
        assert runtime.error == "media_release_failed:RuntimeError"
    else:
        assert modes == ["idle"] and state["owner"] is None
