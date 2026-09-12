"""Real Chromium executes clock mapping with controlled synthetic replies."""

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("V5", "D5")
@pytest.mark.scenario("BROWSER-CLOCK-AUTHENTICATED-EXCHANGE")
def test_controller_clock_is_private_uncached_and_instance_scoped(tmp_path):
    owners = []
    for index in range(2):
        app = create_app(Settings(_env_file=None, data_dir=tmp_path / str(index)), token="test")
        with TestClient(app) as client:
            assert client.get("/api/clock").status_code == 401
            headers = {"Authorization": "Bearer test"}
            response = client.get("/api/clock", headers=headers)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            sample = response.json()
            assert set(sample) == {"time", "owner"} and sample["time"] > 0
            assert client.get("/api/clock", headers=headers).json()["owner"] == sample["owner"]
            owners.append(sample["owner"])
            assert (
                client.get(
                    "/api/clock", headers={**headers, "Origin": "https://example.invalid"}
                ).status_code
                == 403
            )
    assert owners[0] != owners[1]


@pytest.mark.features("V5", "D5")
@pytest.mark.scenario("BROWSER-CLOCK-RESET-EXPIRY-VALIDATION")
def test_browser_clock_round_trip_reset_and_invalid_samples():
    url = (
        "data:text/javascript;base64,"
        + base64.b64encode(Path("reachy_brain/web/static/clock.js").read_bytes()).decode()
    )
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            result = browser.new_page().evaluate(
                """async url => {
                const {BrowserClock} = await import(url);
                let now=10, calls=0;
                const clock = new BrowserClock(()=>now);
                const initially=clock.map();
                await clock.refresh(async(path, options)=> {
                    calls++; if(path!='/api/clock'||options.cache!=='no-store') throw Error('request');
                    now=10.2;return {json:async()=>({time:100.1,owner:'a'.repeat(32)})};
                });
                now=11;const mapped=clock.map(11);
                now=21;const expired=clock.map();
                now=9;const rollback=clock.map();now=11;const revived=clock.map();
                let finish, signal;
                const pending=clock.refresh(async(_,options)=>{signal=options.signal;await new Promise(r=>finish=r);return {json:async()=>({time:100,owner:'b'.repeat(32)})};});
                clock.reset();finish();const accepted=await pending;
                let invalid=false;
                try {await clock.refresh(async()=>({json:async()=>({time:'100',owner:'a'.repeat(32)})}));}
                catch {invalid=true;}
                return {initially,mapped,expired,rollback,revived,aborted:signal.aborted,accepted,after:clock.map(),invalid,calls};
            }""",
                url,
            )
            assert result["mapped"]["time"] == pytest.approx(101)
            assert result["mapped"]["uncertainty"] >= 0.1
            assert result["mapped"]["owner"] == "a" * 32
            assert all(
                result[key] is None
                for key in ["initially", "expired", "rollback", "revived", "after"]
            )
            assert result["aborted"] and not result["accepted"] and result["invalid"]
            assert result["calls"] == 1
        finally:
            browser.close()


@pytest.mark.features("V5", "V9")
@pytest.mark.scenario("BROWSER-CAPTURE-MAPPED-CLOCK")
@pytest.mark.parametrize("reset", [False, True])
def test_capture_retains_sampling_time_through_delayed_encoding(reset):
    url = (
        "data:text/javascript;base64,"
        + base64.b64encode(Path("reachy_brain/web/static/capture.js").read_bytes()).decode()
    )
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            result = browser.new_page().evaluate(
                """async ({url,reset}) => {
                const {captureFrame}=await import(url);
                let finish, uploaded=null, now=10;
                const source={id:'synthetic',generation:0,video:{videoWidth:10,videoHeight:10},
                  canvas:{getContext:()=>({drawImage:()=>{}}),toBlob:f=>finish=f}};
                const clock={generation:1,sample:{received:10},now:()=>now,
                  map:at=>({time:(at??now)+100,uncertainty:.05,owner:'a'.repeat(32)})};
                const pending=captureFrame(source,new Map([[source.id,source]]),async(path,options)=>{
                    uploaded=options.headers;return {json:async()=>({id:'result'})};
                },message=>{throw Error(message);},clock);
                now=12;if(reset)clock.generation++;
                finish(new Blob(['synthetic']));await pending;
                return {uploaded,busy:source.busy};
            }""",
                {"url": url, "reset": reset},
            )
            assert not result["busy"]
            if reset:
                assert result["uploaded"] is None
            else:
                assert result["uploaded"] == {
                    "X-Captured-At": "110",
                    "X-Frame-Sequence": "1",
                    "X-Source-Monotonic": "10",
                    "X-Clock-Uncertainty": "0.05",
                    "X-Clock-Owner": "a" * 32,
                }
        finally:
            browser.close()


@pytest.mark.features("V5", "V9", "D5")
@pytest.mark.scenario("FRAME-CLOCK-METADATA-HTTP")
@pytest.mark.parametrize("case", ["valid", "wrong_owner", "missing", "nan", "negative", "large"])
def test_frame_clock_metadata_is_validated_and_separate_from_exposure(tmp_path, monkeypatch, case):
    import time

    from test_visual_store import image

    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    visual = app.state.visual
    source = visual.source("synthetic", "camera", "Camera")
    calls = []

    def prepare(data):
        calls.append(data)
        return image()

    monkeypatch.setattr(visual, "prepare", prepare)
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer test"}
        owner = client.get("/api/clock", headers=headers).json()["owner"]
        headers.update(
            {
                "X-Captured-At": str(time.time()),
                "X-Clock-Owner": owner,
                "X-Source-Monotonic": "10",
                "X-Clock-Uncertainty": "0.05",
            }
        )
        if case == "wrong_owner":
            headers["X-Clock-Owner"] = "a" * 32
        if case == "missing":
            del headers["X-Source-Monotonic"]
        if case == "nan":
            headers["X-Source-Monotonic"] = "nan"
        if case == "negative":
            headers["X-Clock-Uncertainty"] = "-1"
        if case == "large":
            headers["X-Clock-Uncertainty"] = "11"
        response = client.post(f"/api/frame/{source.id}/0", headers=headers, content=b"synthetic")
        if case == "valid":
            assert response.status_code == 200
            data = response.json()
            assert data["clock_owner"] == owner and data["source_monotonic"] == 10
            assert data["clock_uncertainty_seconds"] == 0.05
            assert data["capture_uncertainty_seconds"] is None
            assert "sensor exposure" in data["timing_note"]
        else:
            assert response.status_code == 400 and not calls and not visual.frames


@pytest.mark.features("V5", "P3", "P10")
@pytest.mark.scenario("BROWSER-DETECTOR-MAPPED-CLOCK")
@pytest.mark.parametrize("reset", [False, True])
def test_production_detector_uses_mapped_sampling_time(reset):
    text = Path("reachy_brain/web/static/app.js").read_text(encoding="utf-8")
    code = text[
        text.index("async function detect(source){") : text.index("$('thumbEnabled').onchange")
    ]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            result = browser.new_page().evaluate(
                """async ({code,reset}) => {
                let finish, uploaded=null, now=10;
                const source={id:'synthetic',generation:0,video:{videoWidth:10,videoHeight:10},
                  detectorCanvas:{getContext:()=>({drawImage:()=>{}}),toBlob:f=>finish=f}};
                const clock={generation:1,sample:{received:10},now:()=>now,
                  map:at=>({time:(at??now)+100,uncertainty:.05,owner:'a'.repeat(32)})};
                const api=async(path,options)=>{uploaded=options.headers;};
                const detect=new Function('sources','captureClock','api','notice','perceptionEnabled',code+';return detect;')(
                    new Map([[source.id,source]]),clock,api,message=>{throw Error(message);},true);
                const pending=detect(source);now=12;if(reset)clock.generation++;
                finish(new Blob(['synthetic']));await pending;
                return {uploaded,busy:source.detectBusy,health:source.detectorHealth};
            }""",
                {"code": code, "reset": reset},
            )
            assert not result["busy"] and result["health"]["errors"] == 0
            if reset:
                assert result["uploaded"] is None
            else:
                assert result["uploaded"] == {
                    "X-Captured-At": "110",
                    "X-Frame-Sequence": "1",
                    "X-Source-Monotonic": "10",
                    "X-Clock-Uncertainty": "0.05",
                    "X-Clock-Owner": "a" * 32,
                }
                assert result["health"]["completed"] == 1
        finally:
            browser.close()


@pytest.mark.features("V5", "C1", "C2")
@pytest.mark.scenario("BROWSER-AUDIO-MAPPED-CLOCK")
def test_audio_processing_times_map_without_wall_clock_reconstruction():
    url = (
        "data:text/javascript;base64,"
        + base64.b64encode(Path("reachy_brain/web/static/clock.js").read_bytes()).decode()
    )
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            result = browser.new_page().evaluate(
                """async url=>{
                const {audioTiming}=await import(url);
                const clock={now:()=>100,map:at=>({time:at+1000,uncertainty:.05,owner:'a'})};
                const context={state:'running',currentTime:20};
                const mapped=audioTiming(clock,context,17,19);
                return {mapped,invalid:[audioTiming(clock,context,19,17),
                    audioTiming(clock,context,NaN,19),audioTiming(clock,context,17,21),
                    audioTiming(clock,{...context,state:'suspended'},17,19),
                    audioTiming({...clock,map:()=>null},context,17,19)]};
            }""",
                url,
            )
            assert result["mapped"] == {
                "capture_start": 1097,
                "capture_end": 1099,
                "clock_uncertainty": 0.05,
            }
            assert all(value is None for value in result["invalid"])
        finally:
            browser.close()


@pytest.mark.features("V5", "C1", "C2", "P10")
@pytest.mark.scenario("PC-UNKNOWN-CLOCK-SPEECH-ONSET")
def test_unknown_clock_onset_stops_and_preserves_speech_priority(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    with TestClient(app) as client:
        with client.websocket_connect(
            "/control", headers={"Origin": "http://127.0.0.1:8765"}
        ) as ws:
            ws.send_text("test")
            assert ws.receive_json()["type"] == "ready"
            core = app.state.active["conversation"]
            core.mode = "conversation"
            ws.send_json({"type": "speech_start", "timing_unknown": True})
            assert ws.receive_json()["type"] == "stop"
            assert core.user_speaking and core.input_capture_active
            assert core.input_capture_start is None and core.input_visual_sources == {}


@pytest.mark.features("V5", "C2", "P10")
@pytest.mark.scenario("PC-SPEECH-ONSET-UNCERTAINTY-POLICY")
@pytest.mark.parametrize(
    "case", ["precise", "boundary", "coarse", "missing", "invalid", "stale", "future"]
)
def test_onset_requires_fresh_bounded_timing_for_visual_binding(tmp_path, case):
    import time

    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    with TestClient(app) as client:
        with client.websocket_connect(
            "/control", headers={"Origin": "http://127.0.0.1:8765"}
        ) as ws:
            ws.send_text("test")
            assert ws.receive_json()["type"] == "ready"
            core = app.state.active["conversation"]
            core.mode = "conversation"
            source = core.visual.source("synthetic", "camera", "Camera")
            message = {"type": "speech_start", "captured": time.time(), "clock_uncertainty": 0.05}
            if case == "boundary":
                message["clock_uncertainty"] = 0.1
            if case == "coarse":
                message["clock_uncertainty"] = 0.101
            if case == "missing":
                del message["clock_uncertainty"]
            if case == "invalid":
                message["clock_uncertainty"] = "private"
            if case == "stale":
                message["captured"] -= 3
            if case == "future":
                message["captured"] += 3
            ws.send_json(message)
            assert ws.receive_json()["type"] == "stop"
            assert core.user_speaking and core.input_capture_active
            if case in ("precise", "boundary"):
                assert core.input_capture_start == message["captured"]
                assert core.input_visual_sources == {source.id: 0}
            else:
                assert core.input_capture_start is None and core.input_visual_sources == {}
