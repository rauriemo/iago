"""Actual browser capture module with controlled synthetic canvas/network boundaries."""

import base64
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


@pytest.mark.features("V1", "V9", "D5")
@pytest.mark.scenario("BROWSER-CAPTURE-SKIPS-AND-GENERATION")
def test_capture_skips_and_late_encoding_are_observable():
    module = Path("reachy_brain/web/static/capture.js").read_bytes()
    url = "data:text/javascript;base64," + base64.b64encode(module).decode()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        try:
            result = page.evaluate(
                """async url => {
              const {captureFrame} = await import(url);
              let finish, uploads = 0;
              const notices = [];
              const source = {id:'synthetic', generation:0, busy:false,
                video:{videoWidth:20, videoHeight:20}, captureStatus:document.createElement('p'),
                canvas:{getContext:()=>({drawImage:()=>{}}),toBlob:callback=>{finish=callback;}}};
              const sources = new Map([[source.id, source]]);
              const api = async()=>{uploads++;return {json:async()=>({id:'frame'})};};
              const run = callback => captureFrame(source,sources,callback||api,message=>notices.push(message));
              const pending = run();
              await run();
              const held = {...source.captureStats};
              finish(new Blob(['synthetic']));
              const accepted = await pending;
              source.video.videoWidth=0; await run(); source.video.videoWidth=20;
              const failed = run(async()=>{throw Error('synthetic upload failure');});
              finish(new Blob(['synthetic'])); await failed;
              const completed = {...source.captureStats};
              const stale = run();
              source.generation++;
              await run(); // Previous generation still owns its one encoding slot.
              finish(new Blob(['synthetic']));
              const staleResult = await stale;
              return {held,accepted,completed,reset:source.captureStats,uploads,notices,
                staleReturned:staleResult!==undefined,busy:source.busy,caption:source.captureStatus.textContent};
            }""",
                url,
            )
            assert result["held"] == {
                "generation": 0,
                "attempts": 1,
                "accepted": 0,
                "busy": 1,
                "unavailable": 0,
                "failed": 0,
            }
            assert result["accepted"] == {"id": "frame"}
            assert result["completed"] == {
                "generation": 0,
                "attempts": 2,
                "accepted": 1,
                "busy": 1,
                "unavailable": 1,
                "failed": 1,
            }
            assert result["reset"] == {
                "generation": 1,
                "attempts": 0,
                "accepted": 0,
                "busy": 1,
                "unavailable": 0,
                "failed": 0,
            }
            assert result["uploads"] == 1 and not result["staleReturned"] and not result["busy"]
            assert result["notices"] == ["synthetic upload failure"]
            assert "1 busy skips" in result["caption"]
        finally:
            browser.close()


@pytest.mark.features("V1", "V9", "C2", "D5")
@pytest.mark.scenario("BROWSER-CAPTURE-UPLOAD-CANCELLATION")
def test_upload_deadline_and_source_stop_abort_pending_network():
    module = Path("reachy_brain/web/static/capture.js").read_bytes()
    url = "data:text/javascript;base64," + base64.b64encode(module).decode()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        try:
            result = page.evaluate(
                """async url => {
              const {captureFrame} = await import(url);
              let timeout, delay, aborted=0;
              const original = window.setTimeout;
              window.setTimeout = (callback, ms) => {timeout=callback;delay=ms;return 123;};
              const notices=[];
              const source={id:'synthetic',generation:0,busy:false,video:{videoWidth:20,videoHeight:20},
                canvas:{getContext:()=>({drawImage:()=>{}}),toBlob:callback=>callback(new Blob(['synthetic']))}};
              const sources=new Map([[source.id,source]]);
              const api=async(path,options)=>new Promise((resolve,reject)=>{
                options.signal.addEventListener('abort',()=>{aborted++;reject(new DOMException('Aborted','AbortError'));});
              });
              try {
                const first=captureFrame(source,sources,api,text=>notices.push(text));
                await Promise.resolve();await Promise.resolve();
                const deadline=delay;
                timeout();await first;
                const timedOut={...source.captureStats};
                const second=captureFrame(source,sources,api,text=>notices.push(text));
                await Promise.resolve();await Promise.resolve();
                sources.delete(source.id);source.captureAbort.abort();await second;
                return {deadline,timedOut,aborted,busy:source.busy,cleared:!source.captureAbort,notices};
              } finally {window.setTimeout=original;}
            }""",
                url,
            )
            assert result["deadline"] == 10000
            assert result["timedOut"]["failed"] == 1 and result["timedOut"]["accepted"] == 0
            assert result["aborted"] == 2 and not result["busy"] and result["cleared"]
            assert result["notices"] == ["Camera or screen upload timed out."]
        finally:
            browser.close()
