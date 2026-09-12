"""Real browser/app with synthetic media; injected ended event is not physical unplug."""

import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("C2", "D1", "D5")
@pytest.mark.scenario("MICROPHONE-TRACK-END-CLEANUP")
def test_microphone_track_end_releases_audio_and_stale_callback_cannot_close_restart(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path, desktop_port=port, perception_enabled=False),
        token="test",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            access_log=False,
            log_level="warning",
            timeout_graceful_shutdown=3,
        )
    )
    worker = threading.Thread(target=server.run, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
            )
            try:
                page = browser.new_page(permissions=["microphone"])
                page.add_init_script("""window.observedMicTracks=[];
                    const capture=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
                    navigator.mediaDevices.getUserMedia=async (...args)=>{
                        const stream=await capture(...args);
                        window.observedMicTracks.push(...stream.getAudioTracks());return stream;
                    };""")
                page.goto(f"http://127.0.0.1:{port}/#token=test")
                page.locator("#aware").click()
                expect(page.locator("#audioSetupStatus")).to_have_text(
                    "Microphone and speaker are ready."
                )
                page.evaluate("""() => {
                    const track=window.observedMicTracks[0];window.staleMicHandler=track.onended;
                    track.stop();track.dispatchEvent(new Event('ended'));
                }""")
                expect(page.locator("#audioSetupStatus")).to_contain_text("Microphone disconnected")
                assert page.evaluate("window.observedMicTracks.every(t=>t.readyState==='ended')")
                deadline = time.monotonic() + 3
                while app.state.active["audio"] is not None and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert app.state.active["audio"] is None
                page.locator("#aware").click()
                expect(page.locator("#audioSetupStatus")).to_have_text(
                    "Microphone and speaker are ready."
                )
                assert page.evaluate("window.observedMicTracks.length") == 2
                page.evaluate("window.staleMicHandler()")
                expect(page.locator("#audioSetupStatus")).to_have_text(
                    "Microphone and speaker are ready."
                )
                assert page.evaluate("window.observedMicTracks[1].readyState") == "live"
                page.locator("#end").click()
                expect(page.locator("#audioSetupStatus")).to_have_text("Audio capture is off.")
                assert page.evaluate("window.observedMicTracks.every(t=>t.onended===null)")
            finally:
                browser.close()
    finally:
        server.should_exit = True
        worker.join(timeout=10)
        assert not worker.is_alive()
