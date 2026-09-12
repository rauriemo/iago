"""Real headless Chromium + loopback server, synthetic media; never a physical PC pass."""

import contextlib
import hashlib
import io
import json
import socket
import threading
import time
from collections import deque

import pytest
import uvicorn
from PIL import Image
from playwright.sync_api import expect, sync_playwright

from reachy_brain.config import Settings
from reachy_brain.integrations.registry import Connection, Rule, Tool
from reachy_brain.web.app import create_app


@pytest.mark.features("D1", "D4", "D6", "V1", "P1", "K1", "E1")
@pytest.mark.scenario("BROWSER-SYNTHETIC-CONTROLS")
def test_browser_camera_projects_and_tools(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = tmp_path / "originals"
    root.mkdir()
    (root / "brief.txt").write_text("Synthetic observatory uses cobalt lenses.")
    installation = tmp_path / "installation.json"
    installation.write_text(
        json.dumps(
            {
                "modules": [
                    {
                        "module": "counter",
                        "account": "synthetic-a",
                        "enabled": False,
                        "transport": "python",
                        "trusted": True,
                        "factory": "reachy_brain.integrations.fake_direct:create",
                    }
                ],
                "skills": [
                    {"path": "examples/workflows/fake-calendar", "trusted": True, "enabled": True}
                ],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path / "data",
            desktop_port=port,
            perception_enabled=True,
            integration_config=installation,
        ),
        token="synthetic-browser-token",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="info",
            access_log=False,
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
                page = browser.new_page(permissions=["camera", "microphone"])
                page.add_init_script("""window.iagoTestSockets=[];
                    window.WebSocket=class extends WebSocket {
                        constructor(...args){super(...args);
                            if(String(args[0]).endsWith('/control'))window.iagoTestSockets.push(this);
                        }
                    };""")
                page.add_init_script(
                    """window.mediaRequests=0;const capture=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.getUserMedia=(...args)=>{window.mediaRequests++;return capture(...args);};"""
                )
                errors = []
                perception_responses = deque(maxlen=12)
                page.on(
                    "response",
                    lambda response: (
                        perception_responses.append(response.status)
                        if "/api/perception/" in response.url
                        else None
                    ),
                )
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{port}/#token=synthetic-browser-token")
                expect(page.locator("#notice")).to_contain_text("Ready")
                assert page.evaluate("window.mediaRequests") == 0
                page.get_by_text("Spontaneous interaction", exact=True).click()
                expect(page.get_by_label("Confirm presence (seconds)", exact=True)).to_have_value(
                    "0.7"
                )
                page.get_by_label("Confirm presence (seconds)", exact=True).fill("1.5")
                page.get_by_label("Confirm absence (seconds)", exact=True).fill("2")
                page.get_by_label("Absence before a new entry greeting (seconds)", exact=True).fill(
                    "5"
                )
                page.get_by_role("button", name="Save behavior settings", exact=True).click()
                expect(page.locator("#notice")).to_have_text("Behavior settings saved.")
                expect(page.get_by_label("Confirm presence (seconds)", exact=True)).to_have_value(
                    "1.5"
                )
                expect(page.get_by_label("Confirm absence (seconds)", exact=True)).to_have_value(
                    "2"
                )
                expect(
                    page.get_by_label("Absence before a new entry greeting (seconds)", exact=True)
                ).to_have_value("5")
                page.get_by_text("Spontaneous interaction", exact=True).click()
                page.locator("#patient").check()
                page.locator("#mute").check()
                page.locator("#volume").evaluate(
                    "element=>{element.value='0.35';element.dispatchEvent(new Event('input',{bubbles:true}));}"
                )
                page.get_by_text("Speech voice", exact=True).click()
                # Synthetic protocol events exercise the real renderer, not a physical voice.
                page.evaluate("""() => {
                    for (const message of [
                        {type:'thinking'}, {type:'answer_partial',text:'Synthetic partial.'},
                        {type:'speech_error',message:'Speech unavailable. The answer will continue as text.'},
                        {type:'answer',text:'Synthetic complete answer.'}
                    ]) window.iagoTestSockets[0].onmessage({data:JSON.stringify(message)});
                }""")
                expect(page.locator("#notice")).to_have_text(
                    "Speech unavailable. Answer shown as text."
                )
                expect(page.get_by_text("Synthetic complete answer.", exact=True)).to_be_visible()
                page.locator("#voicePreview").click()
                expect(page.locator("#notice")).to_contain_text(
                    "Start Conversation before previewing"
                )
                assert page.evaluate("window.mediaRequests") == 0
                page.locator("#voiceProvider").select_option("openai")
                expect(page.locator("#voiceStatus")).to_have_text(
                    "Saved. Applies at the next conversation start."
                )
                page.get_by_text("Personality & language", exact=True).click()
                page.locator("#personalityText").fill(
                    "Speak concise English and respectfully challenge assumptions."
                )
                page.get_by_role("button", name="Save personality", exact=True).click()
                expect(page.locator("#personalityStatus")).to_have_text("Saved for future replies.")
                page.locator("#aware").click()
                expect(page.locator("#state")).to_contain_text("aware")
                page.evaluate("""() => {
                    window.startupTracks=[];
                    const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
                    navigator.mediaDevices.getUserMedia=async (...args)=>{
                        const stream=await original(...args);
                        if(args[0].video)window.startupTracks.push(...stream.getTracks());
                        return stream;
                    };
                }""")
                page.route(
                    "**/api/source",
                    lambda route: route.fulfill(
                        status=503,
                        content_type="application/json",
                        body='{"detail":"synthetic registration failure"}',
                    ),
                )
                page.locator("#camera").click()
                expect(page.locator("#notice")).to_contain_text("synthetic registration failure")
                page.wait_for_function(
                    "window.startupTracks.length > 0 && window.startupTracks.every(t=>t.readyState==='ended')"
                )
                expect(page.locator("#previews video")).to_have_count(0)
                page.unroute("**/api/source")
                page.evaluate("""() => {
                    const original=HTMLMediaElement.prototype.play;
                    HTMLMediaElement.prototype.play=function(){
                        HTMLMediaElement.prototype.play=original;
                        return Promise.reject(new Error('synthetic preview failure'));
                    };
                }""")
                page.locator("#camera").click()
                expect(page.locator("#notice")).to_contain_text("synthetic preview failure")
                page.wait_for_function("window.startupTracks.every(t=>t.readyState==='ended')")
                expect(page.locator("#previews video")).to_have_count(0)
                assert not app.state.visual.sources
                page.evaluate("""() => {
                    window.pendingCameraReady=false;
                    const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
                    navigator.mediaDevices.getUserMedia=async (...args)=>{
                        navigator.mediaDevices.getUserMedia=original;
                        const stream=await original(...args);
                        window.pendingCameraReady=true;
                        return await new Promise(resolve=>{window.releaseCamera=()=>resolve(stream);});
                    };
                }""")
                page.locator("#camera").click()
                page.wait_for_function("window.pendingCameraReady")
                requests = page.evaluate("window.mediaRequests")
                page.locator("#camera").click()
                assert page.evaluate("window.mediaRequests") == requests
                page.locator("#end").click()
                expect(page.locator("#state")).to_contain_text("idle")
                page.evaluate("window.releaseCamera()")
                page.wait_for_function("window.startupTracks.every(t=>t.readyState==='ended')")
                expect(page.locator("#previews video")).to_have_count(0)
                assert not app.state.visual.sources
                page.locator("#aware").click()
                expect(page.locator("#state")).to_contain_text("aware")
                page.locator("#camera").click()
                expect(page.locator("#previews video")).to_have_count(1)
                expect(page.locator("#storage")).to_contain_text("Retained", timeout=15000)
                expect(page.locator("#storage")).to_contain_text("gaps possible")
                visual = app.state.visual
                before_look = set(visual.frames)
                page.evaluate("""() => {
                    window.addEventListener('iago-history-selected', event=>window.lookNowFrame=event.detail.id);
                }""")
                page.locator("#lookNow").click()
                expect(page.locator("#historySelection")).to_contain_text("fresh capture")
                selected_frame = page.evaluate("window.lookNowFrame")
                assert selected_frame not in before_look
                assert visual.get(selected_frame).source == next(iter(visual.sources))
                page.locator("#forgetHistorySelection").click()
                # Synthetic robot-profile UI announcement; the real HTTP capture is
                # routed to this browser's synthetic camera, not physical Reachy.
                camera_source = next(iter(visual.sources))
                page.route(
                    "**/api/capture",
                    lambda route: route.continue_(
                        url=route.request.url + f"?source={camera_source}"
                    ),
                )
                page.evaluate("""() => window.iagoTestSockets[0].onmessage({data:JSON.stringify({
                    type:'ready',deployment:'reachy_pc',robot_camera_enabled:true
                })})""")
                before_robot_look = set(visual.frames)
                page.locator("#robotLookNow").click()
                expect(page.locator("#historySelection")).to_contain_text("new capture")
                assert page.evaluate("window.lookNowFrame") not in before_robot_look
                page.locator("#forgetHistorySelection").click()
                page.unroute("**/api/capture")
                page.evaluate("""() => window.iagoTestSockets[0].onmessage({data:JSON.stringify({
                    type:'ready',deployment:'desktop'
                })})""")
                expect(page.locator("#robotLookNow")).to_be_hidden()
                for _attempt in range(2):
                    page.locator("#notice").evaluate("el=>el.textContent='Waiting for this upload'")
                    page.locator("#upload").set_input_files(
                        {"name": "invalid.png", "mimeType": "image/png", "buffer": b"not an image"}
                    )
                    expect(page.locator("#notice")).to_contain_text("invalid_image")
                    assert len(visual.sources) == 1, "failed upload must release its source slot"
                    assert all(s.kind == "camera" for s in visual.sources.values())
                    expect(page.locator("#previews video")).to_have_count(1)
                    assert page.locator("#upload").input_value() == ""
                short_image = io.BytesIO()
                Image.new("RGB", (32, 32), "orange").save(short_image, format="PNG")

                def short_thumbnail_expiry(route):
                    response = route.fetch()
                    route.fulfill(
                        response=response, headers={**response.headers, "x-iago-expires-in": "1.0"}
                    )

                page.route("**/api/frame/*?thumbnail=true", short_thumbnail_expiry)
                page.locator("#upload").set_input_files(
                    {
                        "name": "short-preview.png",
                        "mimeType": "image/png",
                        "buffer": short_image.getvalue(),
                    }
                )
                recent_short = page.locator('#history img[alt^="short-preview.png"]')
                expect(recent_short).to_have_count(1)
                uploaded = next(
                    s for s in visual.sources.values() if s.label == "short-preview.png"
                )
                uploaded_frame = next(f for f in visual.frames.values() if f.source == uploaded.id)
                assert uploaded_frame.transformation["stored_format"] == "PNG"
                with Image.open(io.BytesIO(uploaded_frame.image)) as decoded:
                    assert decoded.tobytes() == Image.new("RGB", (32, 32), "orange").tobytes()
                expect(recent_short).to_have_count(0, timeout=2000)
                page.unroute("**/api/frame/*?thumbnail=true", short_thumbnail_expiry)
                held_registration = []
                page.evaluate("""() => {
                    window.uploadNotices=[];
                    new MutationObserver(()=>window.uploadNotices.push(document.getElementById('notice').textContent))
                        .observe(document.getElementById('notice'),{childList:true,subtree:true,characterData:true});
                }""")
                page.route("**/api/source", lambda route: held_registration.append(route))
                page.locator("#upload").set_input_files(
                    {
                        "name": "a" * 119 + "😀.png",
                        "mimeType": "image/png",
                        "buffer": short_image.getvalue(),
                    }
                )
                deadline = time.monotonic() + 5
                while not held_registration:
                    assert time.monotonic() < deadline
                    page.wait_for_timeout(20)
                with page.expect_response(lambda response: response.url.endswith("/api/visual")):
                    page.locator("#clear").click()
                registered = held_registration[0].fetch()
                late_source = registered.json()["id"]
                assert registered.json()["label"] == "a" * 119 + "😀"
                with page.expect_response(
                    lambda response: (
                        response.url.endswith("/api/visual")
                        and response.request.post_data_json.get("source") == late_source
                    )
                ):
                    held_registration[0].fulfill(response=registered)
                page.unroute("**/api/source")
                page.wait_for_function(
                    "window.uploadNotices.some(text=>text.includes('Image upload canceled'))"
                )
                assert late_source not in visual.sources
                assert not any(f.source == late_source for f in visual.frames.values())
                expect(page.locator("#previews video")).to_have_count(1)
                archive = visual.source("synthetic-history", "upload", "Archived sheet")
                encoded = io.BytesIO()
                Image.new("RGB", (80, 60), "white").save(encoded, format="PNG")
                prepared = visual.prepare(encoded.getvalue(), preserve_png=True)
                captured = time.time() - 100
                for index in range(30):
                    visual.add(archive.id, 0, captured + index, prepared)
                evidence_frame = list(visual.frames.values())[-1]
                evidence_rows = [
                    visual.describe(evidence_frame),
                    {**visual.describe(evidence_frame), "region": [0, 0, 40, 30]},
                    {
                        "path": "synthetic.txt",
                        "project": "Synthetic project",
                        "locator_kind": "line",
                        "locator": "1",
                        "revision": "synthetic-revision",
                        "text": "Synthetic cited passage.",
                    },
                ]

                def emit_evidence(rows):
                    page.evaluate(
                        "rows=>window.iagoTestSockets[0].dispatchEvent(new MessageEvent('message',{data:JSON.stringify({type:'evidence',frames:rows})}))",
                        rows,
                    )

                emit_evidence(evidence_rows)
                expect(page.locator("#evidence img")).to_have_count(2)
                expect(page.locator("#evidence .evidenceCrop")).to_have_count(1)
                expect(page.locator("#evidence blockquote")).to_have_text(
                    "Synthetic cited passage."
                )
                expect(page.locator("#evidence .evidenceCrop")).to_have_css("width", "40px")
                page.get_by_role("button", name="View full image", exact=True).first.click()
                expect(
                    page.get_by_role("button", name="View full image", exact=True).first
                ).to_be_enabled()
                expect(page.locator('#evidence [data-full-image="true"]')).to_have_count(1)
                page.get_by_role("button", name="View full image", exact=True).nth(1).click()
                expect(
                    page.get_by_role("button", name="View full image", exact=True).nth(1)
                ).to_be_enabled()
                expect(page.locator('#evidence [data-full-image="true"]')).to_have_count(1)
                page.get_by_text("Browse older images", exact=True).click()
                page.get_by_role("button", name="Refresh history", exact=True).click()
                expect(page.locator("#historyPages .frame")).to_have_count(24)
                page.locator("#historySource").select_option(archive.id)
                page.get_by_role("button", name="Refresh history", exact=True).click()
                expect(page.locator("#historyPages .frame")).to_have_count(24)
                expect(page.locator("#historyMessage")).to_contain_text("24 retained")
                expect(page.locator("#historyCoverage")).to_contain_text("rolling images")
                expect(page.locator("#historyCoverage")).to_contain_text("Snapshot at refresh")
                expect(page.locator("#historyCoverage")).to_contain_text("PC upload requests:")
                page.locator("#historyOlder").click()
                expect(page.locator("#historyPages .frame")).to_have_count(6)
                expect(page.locator("#historyOlder")).to_be_disabled()
                page.locator("#historyPages button").filter(has_text="Inspect image").first.click()
                expect(page.locator("#historyImage img")).to_have_count(1)
                with page.expect_download() as saved_image:
                    page.get_by_role("link", name="Save image", exact=True).click()
                download = saved_image.value
                assert download.suggested_filename == f"{evidence_frame.image_sha256}.png"
                exported_image = tmp_path / download.suggested_filename
                download.save_as(exported_image)
                assert exported_image.read_bytes() == evidence_frame.image
                assert (
                    hashlib.sha256(exported_image.read_bytes()).hexdigest()
                    == evidence_frame.image_sha256
                )
                expect(page.locator("#historyImage")).to_contain_text("Archived sheet")
                page.evaluate(
                    "window.iagoCrop=null;window.addEventListener('iago-history-selected',e=>window.iagoCrop=e.detail.region||null)"
                )
                original_image = page.locator("#historyImage img")
                original_image.scroll_into_view_if_needed()
                box = original_image.bounding_box()
                page.mouse.move(box["x"] + box["width"] * 0.2, box["y"] + box["height"] * 0.25)
                page.mouse.down()
                page.mouse.move(box["x"] + box["width"] * 0.75, box["y"] + box["height"] * 0.75)
                page.mouse.up()
                expect(page.locator("#historyUseCrop")).to_be_enabled()
                page.locator("#historyUseCrop").click()
                expect(page.locator("#historySelection")).to_contain_text("selected crop")
                x, y, width, height = page.evaluate("window.iagoCrop")
                assert 0 <= x < x + width <= 80 and 0 <= y < y + height <= 60
                page.get_by_role("textbox", name="Pinned image label", exact=True).first.fill(
                    "Board revision B <draft>"
                )
                page.get_by_role("button", name="Keep image", exact=True).first.click()
                expect(page.get_by_role("button", name="Unpin image", exact=True)).to_have_count(1)
                expect(
                    page.get_by_role("textbox", name="Pinned image label", exact=True).first
                ).to_have_value("Board revision B <draft>")
                expect(
                    page.get_by_role("textbox", name="Pinned image label", exact=True).first
                ).to_be_disabled()
                page.get_by_role("button", name="Refresh history", exact=True).click()
                expect(page.locator("#historyPages .frame")).to_have_count(24)
                page.locator("#historyOlder").click()
                expect(page.locator("#historyPages .frame")).to_have_count(6)
                expect(
                    page.get_by_role("textbox", name="Pinned image label", exact=True).first
                ).to_have_value("Board revision B <draft>")
                page.get_by_role("button", name="Use for typed question", exact=True).first.click()
                expect(page.locator("#historySelection")).to_contain_text("Archived sheet")
                page.locator("#forgetHistorySelection").click()
                expect(page.locator("#historySelection")).to_be_empty()
                page.get_by_role("button", name="Use for typed question", exact=True).first.click()
                held = []
                held_thumbnails = []
                archive_ids = {f.id for f in visual.frames.values() if f.source == archive.id}

                def hold_original(route):
                    if "thumbnail=true" in route.request.url:
                        frame_id = route.request.url.split("/api/frame/")[1].split("?")[0]
                        if frame_id in archive_ids:
                            held_thumbnails.append((route, route.fetch()))
                        else:
                            route.continue_()
                    else:
                        held.append((route, route.fetch()))

                page.route("**/api/frame/*", hold_original)
                page.get_by_role("button", name="Inspect image", exact=True).first.click()
                page.get_by_role("button", name="View full image", exact=True).first.click()
                deadline = time.monotonic() + 8
                while not held or not held_thumbnails:
                    assert time.monotonic() < deadline
                    page.wait_for_timeout(20)
                page.evaluate("""() => {
                    window.restoredArchiveImages=[];
                    new MutationObserver(records=>{
                        for(const record of records)for(const node of record.addedNodes){
                            if(!(node instanceof Element))continue;
                            const images=node.matches('img')?[node]:[...node.querySelectorAll('img')];
                            for(const image of images)if(image.alt.startsWith('Archived sheet'))
                                window.restoredArchiveImages.push(image.alt);
                        }
                    }).observe(document.getElementById('history'),{childList:true,subtree:true});
                }""")
                page.locator("#clear").click()
                expect(page.locator("#historySelection")).to_be_empty()
                expect(page.locator("#historyImage img")).to_have_count(0)
                expect(page.locator("#evidence img")).to_have_count(0)
                for route, response in held + held_thumbnails:
                    with contextlib.suppress(Exception):
                        route.fulfill(response=response)
                page.unroute("**/api/frame/*", hold_original)
                page.wait_for_timeout(100)
                expect(page.locator("#historyPages .frame")).to_have_count(0)
                expect(page.locator("#historyCoverage p")).to_have_count(0)
                expect(page.locator("#historyImage img")).to_have_count(0)
                expect(page.locator('#history img[alt^="Archived sheet"]')).to_have_count(0)
                assert page.evaluate("window.restoredArchiveImages") == []
                assert exported_image.read_bytes() == evidence_frame.image
                expect(page.get_by_role("link", name="Save image", exact=True)).to_have_count(0)
                fleeting = visual.add(
                    archive.id,
                    archive.generation,
                    time.time() - visual.retention + 3,
                    prepared,
                )
                fleeting.labels = ["short-lived"]
                emit_evidence([visual.describe(fleeting)])
                expect(page.locator("#evidence img")).to_have_count(1)
                page.locator("#historyQuery").fill("short-lived")
                page.get_by_role("button", name="Refresh history", exact=True).click()
                expect(page.locator("#historyPages .frame")).to_have_count(1)
                page.get_by_role("button", name="Inspect image", exact=True).click()
                expect(page.locator("#historyImage img")).to_have_count(1)
                expect(page.locator("#historyPages .frame")).to_have_count(0, timeout=7000)
                expect(page.locator("#historyImage img")).to_have_count(0)
                expect(page.locator("#historyMessage")).to_contain_text("expired")
                expect(page.locator("#evidence img")).to_have_count(0)
                expect(page.locator("#evidence")).to_contain_text("expired")
                expect(page.locator("#history img").first).to_be_visible(timeout=10000)
                try:
                    expect(page.locator("#perception")).to_contain_text(
                        "analyzed fps", timeout=15000
                    )
                except AssertionError as exc:
                    health = page.request.get(
                        f"http://127.0.0.1:{port}/api/status",
                        headers={"Authorization": "Bearer synthetic-browser-token"},
                    ).json()["perception"]["worker"]
                    browser_health = page.evaluate(
                        "async () => (await import('/static/app.js')).detectorSnapshot()"
                    )
                    raise AssertionError(
                        f"Perception feedback missing; recent HTTP statuses: {list(perception_responses)}; worker: {health}; browser: {browser_health}"
                    ) from exc
                browser_health = page.evaluate(
                    "async () => (await import('/static/app.js')).detectorSnapshot()"
                )
                assert browser_health["enabled"] and browser_health["sources"]
                assert browser_health["sources"][0]["completed"] > 0
                page.evaluate(
                    "async () => { window.detectorSnapshotForTest = (await import('/static/app.js')).detectorSnapshot; }"
                )
                held_detector = []
                page.route("**/api/perception/**", lambda route: held_detector.append(route))
                try:
                    page.wait_for_function(
                        "() => window.detectorSnapshotForTest().sources.some(s => s.phase === 'uploading' && s.busy)",
                        timeout=5000,
                    )
                    page.wait_for_function(
                        "() => window.detectorSnapshotForTest().sources.some(s => s.phase === 'uploading' && s.phase_seconds > 0.3)",
                        timeout=5000,
                    )
                    held_health = page.evaluate(
                        "async () => (await import('/static/app.js')).detectorSnapshot()"
                    )["sources"][0]
                    assert held_detector and held_health["submitted"] > held_health["completed"]
                    assert held_health["busy_skips"] > 0
                finally:
                    for route in list(held_detector):
                        route.continue_()
                    page.unroute("**/api/perception/**")
                page.wait_for_function(
                    "completed => window.detectorSnapshotForTest().sources.some(s => s.completed > completed)",
                    arg=held_health["completed"],
                    timeout=5000,
                )
                before_timeout = page.evaluate("() => window.detectorSnapshotForTest().sources[0]")
                stalled_detector = []
                page.route("**/api/perception/**", lambda route: stalled_detector.append(route))
                try:
                    page.wait_for_function(
                        "count => window.detectorSnapshotForTest().sources.some(s => s.timeouts > count)",
                        arg=before_timeout["timeouts"],
                        timeout=4000,
                    )
                    assert stalled_detector
                    after_timeout = page.evaluate(
                        "() => window.detectorSnapshotForTest().sources[0]"
                    )
                    assert after_timeout["submitted"] > before_timeout["submitted"]
                finally:
                    page.unroute("**/api/perception/**")
                page.wait_for_function(
                    "completed => window.detectorSnapshotForTest().sources.some(s => s.completed > completed)",
                    arg=after_timeout["completed"],
                    timeout=5000,
                )
                page.get_by_text("Devices & session details", exact=True).click()
                saved_devices = {}
                for control_id in ["microphone", "cameraDevice", "speaker"]:
                    control = page.locator(f"#{control_id}")
                    choice = control.evaluate(
                        "element=>[...element.options].find(option=>option.value)?.value||''"
                    )
                    control.select_option(choice)
                    saved_devices[control_id] = choice
                page.get_by_text("Project documents", exact=True).click()
                page.locator("#projectName").fill("Observatory")
                page.locator("#projectRoot").fill(str(root))
                page.get_by_role("button", name="Add folder", exact=True).click()
                page.get_by_role("button", name="Use", exact=True).click()
                expect(page.locator("#projects")).to_contain_text("indexed", timeout=15000)
                page.locator("#documentQuery").fill("cobalt")
                page.get_by_role("button", name="Search", exact=True).click()
                expect(page.locator("#documentResults")).to_contain_text("cobalt lenses")
                expect(page.locator("#documentResults")).to_contain_text("brief.txt")
                page.get_by_text("Integrations & workflows", exact=True).click()
                expect(page.locator("#integrations")).to_contain_text(
                    "documents__active__search_project_documents"
                )
                permission = page.get_by_label(
                    "Permission for documents__active__search_project_documents", exact=True
                )
                with page.expect_response(
                    lambda response: (
                        response.url.endswith("/api/integrations")
                        and response.request.method == "POST"
                    )
                ) as saved:
                    permission.select_option("deny")
                assert saved.value.status == 200
                expect(permission).to_have_value("deny")
                workflow = page.get_by_label("Enable workflow fake-calendar-review", exact=True)
                module = page.get_by_label("Enable module counter / synthetic-a", exact=True)
                with page.expect_response(
                    lambda response: (
                        response.url.endswith("/api/integrations")
                        and response.request.method == "POST"
                    )
                ) as saved:
                    module.check()
                assert saved.value.status == 200
                expect(page.locator("#integrations")).to_contain_text(
                    "Restart application to connect"
                )
                with page.expect_response(
                    lambda response: (
                        response.url.endswith("/api/integrations")
                        and response.request.method == "POST"
                    )
                ) as saved:
                    workflow.uncheck()
                assert saved.value.status == 200
                page.reload()
                expect(page.locator("#patient")).to_be_checked()
                expect(page.locator("#mute")).to_be_checked()
                expect(page.locator("#volume")).to_have_value("0.35")
                for control_id, choice in saved_devices.items():
                    expect(page.locator(f"#{control_id}")).to_have_value(choice)
                assert page.evaluate("window.mediaRequests") == 0
                expect(page.locator("#voiceProvider")).to_have_value("openai")
                expect(page.locator("#personalityText")).to_have_value(
                    "Speak concise English and respectfully challenge assumptions."
                )
                page.get_by_text("Integrations & workflows", exact=True).click()
                expect(permission).to_have_value("deny")
                expect(workflow).not_to_be_checked()
                expect(module).to_be_checked()
                with page.expect_response(
                    lambda response: (
                        response.url.endswith("/api/integrations")
                        and response.request.method == "POST"
                    )
                ) as saved:
                    permission.select_option("allow")
                assert saved.value.status == 200
                expect(permission).to_have_value("allow")
                journal = app.state.executor.operations
                journal.prepare("browser-cancel", "synthetic-digest", "fake__local__write", 0)
                page.locator("#refreshOperations").click()
                row = page.locator('[data-operation="browser-cancel"]')
                expect(row).to_contain_text("proposed")
                row.get_by_role("button", name="Cancel before dispatch", exact=True).click()
                expect(row).to_contain_text("canceled-before-dispatch")
                expect(row.get_by_role("button")).to_have_count(0)
                executor = app.state.executor
                connection = Connection("synthetic", "local")
                executor.registry.add_connection(connection)
                canceled = []

                async def fake_request(payload, context):
                    canceled.append(payload["operation_id"])
                    return {"outcome": "too_late", "provider_ref": context.operation_id}

                cancel_tool = Tool(
                    "synthetic",
                    "local",
                    "cancel",
                    "Synthetic cancellation",
                    {
                        "type": "object",
                        "properties": {"operation_id": {"type": "string"}},
                        "required": ["operation_id"],
                    },
                    {"type": "object"},
                    fake_request,
                    action="write",
                )
                original = Tool(
                    "synthetic",
                    "local",
                    "write",
                    "Synthetic seeded operation",
                    {"type": "object"},
                    {"type": "object"},
                    fake_request,
                    action="write",
                    cancel_tool=cancel_tool.key,
                )
                executor.registry.register(original)
                executor.registry.register(cancel_tool)
                executor.policy.set(Rule(cancel_tool.key, "write", "confirm"))
                journal.begin(
                    "browser-provider",
                    "synthetic-seed",
                    original.key,
                    0,
                    executor.account_ref(connection),
                )
                journal.finish("browser-provider", "uncertain")
                page.locator("#refreshOperations").click()
                provider_row = page.locator('[data-operation="browser-provider"]')
                dialogs = []

                def approve(dialog):
                    dialogs.append(dialog.message)
                    dialog.accept()

                page.once("dialog", approve)
                provider_row.get_by_role(
                    "button", name="Request provider cancellation", exact=True
                ).click()
                expect(page.locator("#notice")).to_contain_text("too_late")
                assert canceled == ["browser-provider"]
                assert len(dialogs) == 1 and '"operation_id":"browser-provider"' in dialogs[0]
                assert "Account: local" in dialogs[0]
                assert journal.get("browser-provider")["status"] == "uncertain"
                page.locator("#exportResources").evaluate(
                    "button=>button.closest('details').open=true"
                )
                with page.expect_download() as downloaded:
                    page.locator("#exportResources").click()
                resource_file = tmp_path / "resource-export.json"
                downloaded.value.save_as(resource_file)
                resource_data = json.loads(resource_file.read_text(encoding="utf-8"))
                assert 0 < len(resource_data["samples"]) <= resource_data["capacity"]
                assert resource_data["latest"]["backend_rss_bytes"] > 0
                page.locator("#end").click()
                expect(page.locator("#state")).to_contain_text("idle")
                expect(page.locator("#previews video")).to_have_count(0)
                page.evaluate(
                    "localStorage.setItem('iago-device-preferences',JSON.stringify({microphone:'synthetic-missing-device',volume:99,patient:'invalid'}))"
                )
                page.reload()
                expect(page.locator("#microphone")).to_have_value("synthetic-missing-device")
                expect(page.locator("#microphone option:checked")).to_have_text(
                    "Saved device unavailable — choose another"
                )
                expect(page.locator("#volume")).to_have_value("0.8")
                expect(page.locator("#patient")).not_to_be_checked()
                page.locator("#aware").click()
                expect(page.locator("#state")).to_contain_text("aware")
                assert page.evaluate("window.mediaRequests") == 0
                page.locator("#end").click()
                expect(page.locator("#state")).to_contain_text("idle")
                assert not errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        worker.join(15)
        assert not worker.is_alive()
