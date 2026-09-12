"""Opt-in native Windows browser acquisition, with no fake media or cloud requests."""

import os
import socket
import sys
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.live_pc
@pytest.mark.features("D1", "D5", "V1", "V3")
@pytest.mark.scenario("PC-NATIVE-BROWSER-MEDIA-ACQUISITION")
def test_native_browser_media_acquisition_and_camera_clear(tmp_path, record_property):
    if os.environ.get("IAGO_PC_NATIVE_MEDIA_CHECK") != "1":
        pytest.skip("IAGO_PC_NATIVE_MEDIA_CHECK=1 enables brief real camera/microphone acquisition")
    if sys.platform != "win32":
        pytest.skip("Native Windows PC required")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            desktop_port=port,
            perception_enabled=False,
            openai_api_key="",
            elevenlabs_api_key="",
            iago_development_budget=0,
        ),
        token="native-media-probe",
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
    measured = {
        "fixture": "actual Windows browser devices; no fake media flags",
        "scope": "Acquisition and camera clearing only; no optical, perception, acoustic or speech qualification",
    }
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel="chromium")
            try:
                measured["browser"] = browser.version
                page = browser.new_page(permissions=["camera", "microphone"])
                page.goto(f"http://127.0.0.1:{port}/#token=native-media-probe")
                page.locator("#aware").click()
                expect(page.locator("#state")).to_contain_text("aware")
                measured["available_devices"] = page.evaluate("""async () =>
                    (await navigator.mediaDevices.enumerateDevices()).map(d=>({kind:d.kind,label:d.label}))
                """)
                measured["microphone"] = page.evaluate("""async () => {
                    const stream=await navigator.mediaDevices.getUserMedia({audio:true,video:false});
                    let ctx;
                    try {
                        const t=stream.getAudioTracks()[0];ctx=new AudioContext();await ctx.resume();
                        const source=ctx.createMediaStreamSource(stream), analyser=ctx.createAnalyser();
                        source.connect(analyser);analyser.fftSize=1024;
                        const samples=new Float32Array(1024);let peak=0,windows=0;
                        for(let n=0;n<20;n++){
                            await new Promise(resolve=>setTimeout(resolve,50));
                            analyser.getFloatTimeDomainData(samples);
                            for(const value of samples)peak=Math.max(peak,Math.abs(value));windows++;
                        }
                        return {label:t.label,ready_state:t.readyState,sample_rate:ctx.sampleRate,
                                observation_windows:windows,peak,muted:t.muted};
                    } finally {stream.getTracks().forEach(t=>t.stop());if(ctx)await ctx.close();}
                }""")
                assert measured["microphone"]["ready_state"] == "live"
                assert measured["microphone"]["observation_windows"] == 20
                assert not measured["microphone"]["muted"]
                page.locator("#camera").click()
                page.wait_for_function(
                    """() => {
                    if(document.querySelector('#notice').textContent.startsWith('camera:'))return true;
                    const v=document.querySelector('#previews video');
                    return v && v.videoWidth>0 && v.srcObject?.getVideoTracks()[0].readyState==='live';
                }""",
                    timeout=15000,
                )
                measured["camera_notice"] = page.locator("#notice").inner_text()
                assert page.locator("#previews video").count(), measured["camera_notice"]
                measured["camera"] = page.evaluate("""() => {
                    const v=document.querySelector('#previews video');
                    const t=v.srcObject.getVideoTracks()[0]; window.probeCameraTrack=t;
                    const s=t.getSettings();
                    return {label:t.label,width:v.videoWidth,height:v.videoHeight,
                            frame_rate:s.frameRate,ready_state:t.readyState};
                }""")
                page.wait_for_function(
                    """() => document.querySelector('#previews video')?.currentTime>2"""
                )
                assert len(app.state.visual.frames) >= 1, "No actual camera image archived"
                measured["archived_frames"] = len(app.state.visual.frames)
                page.locator("#previews figure button").click()
                page.wait_for_function("() => window.probeCameraTrack.readyState==='ended'")
                expect(page.locator("#previews video")).to_have_count(0)
                # Backend source clearing is asynchronous; wait for the actual browser request.
                deadline = time.monotonic() + 3
                while app.state.visual.frames and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert not app.state.visual.frames
                measured["camera_stopped_and_cleared"] = True
            finally:
                browser.close()
    finally:
        server.should_exit = True
        worker.join(timeout=10)
        record_property("measurements", measured)
        record_property("sample_count", measured.get("archived_frames", 0))
        assert not worker.is_alive(), "Native media probe backend did not stop"
