"""Real Chromium audio resources with injected delays/failures; synthetic devices only."""

import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("C2", "D1", "D4")
@pytest.mark.scenario("BROWSER-AUDIO-SETUP-OWNERSHIP")
@pytest.mark.parametrize("failure", ["permission_end", "worklet_failure", "channel_failure"])
def test_partial_audio_setup_cleanup_and_end(tmp_path, failure):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, desktop_port=port), token="test")
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
            time.sleep(0.02)
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
            )
            try:
                page = browser.new_page(permissions=["microphone"])
                page.add_init_script("""
window.streams=[];window.contexts=[];window.holdCapture=false;window.failWorklet=false;window.failAudioChannel=false;
const Socket=window.WebSocket;
window.WebSocket=class extends Socket{constructor(url,...args){super(window.failAudioChannel&&url.endsWith('/audio')?url+'-synthetic-unavailable':url,...args);}};
const capture=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia=async(...args)=>{const stream=await capture(...args);window.streams.push(stream);if(window.holdCapture)return await new Promise(resolve=>window.releaseCapture=()=>resolve(stream));return stream;};
const Original=window.AudioContext;
window.AudioContext=class extends Original{constructor(...args){super(...args);window.contexts.push(this);if(window.failWorklet)this.audioWorklet.addModule=()=>Promise.reject(Error('Synthetic worklet initialization failure'));}};
""")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{port}/#token=test")
                expect(page.locator("#notice")).to_contain_text("Ready")
                if failure == "permission_end":
                    page.evaluate("window.holdCapture=true")
                    page.locator("#aware").click()
                    page.wait_for_function("typeof window.releaseCapture==='function'")
                    page.locator("#aware").click()
                    assert page.evaluate("window.streams.length") == 1
                    page.locator("#end").click()
                    expect(page.locator("#state")).to_contain_text("idle")
                    page.evaluate("window.releaseCapture()")
                    page.wait_for_function(
                        "window.streams.every(stream=>stream.getTracks().every(track=>track.readyState==='ended'))"
                    )
                    assert page.evaluate("window.contexts.length") == 0
                    expect(page.locator("#state")).to_contain_text("idle")
                    page.evaluate("window.holdCapture=false")
                else:
                    page.evaluate(
                        "window.failWorklet=true"
                        if failure == "worklet_failure"
                        else "window.failAudioChannel=true"
                    )
                    page.locator("#aware").click()
                    expect(page.locator("#state")).to_contain_text("aware")
                    page.wait_for_function(
                        "window.contexts.length===1&&window.contexts[0].state==='closed'&&window.streams[0].getTracks().every(track=>track.readyState==='ended')"
                    )
                    expect(page.locator("#audioSetupStatus")).to_contain_text("Audio setup failed")
                    expect(page.locator("#notice")).to_contain_text("Audio setup failed")
                    page.evaluate("window.failWorklet=false;window.failAudioChannel=false")
                # A fresh attempt after either failure acquires a usable context and ends cleanly.
                page.locator("#aware").click()
                page.wait_for_function("window.contexts.some(context=>context.state==='running')")
                expect(page.locator("#audioSetupStatus")).to_have_text(
                    "Microphone and speaker are ready."
                )
                expect(page.locator("#state")).to_contain_text("aware")
                page.locator("#end").click()
                page.wait_for_function(
                    "window.contexts.every(context=>context.state==='closed')&&window.streams.every(stream=>stream.getTracks().every(track=>track.readyState==='ended'))"
                )
                expect(page.locator("#state")).to_contain_text("idle")
                assert not errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        worker.join(15)
        assert not worker.is_alive()
