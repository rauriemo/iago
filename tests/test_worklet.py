"""Run the shipped sink code in Chromium with a deterministic synthetic render clock."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


@pytest.mark.features("C2", "C8", "D5", "E1")
@pytest.mark.scenario("WORKLET-LOCAL-STOP-RACES")
def test_local_sink_stale_audio_lease_and_consumption():
    source = Path("reachy_brain/web/static/audio-worklet.js").read_text()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            results = page.evaluate(
                """source => {
                globalThis.currentTime=0; globalThis.sampleRate=48000;
                const events=[];
                globalThis.AudioWorkletProcessor=class {constructor(){this.port={postMessage:m=>events.push(m)};}};
                globalThis.registerProcessor=(name,klass)=>{globalThis.Sink=klass;};
                (0,eval)(source);
                const sink=new Sink(),send=m=>sink.port.onmessage({data:m});
                const render=()=>{const out=new Float32Array(128);sink.process([],[[out]]);currentTime+=128/48000;return Array.from(out);};
                send({type:'authorize',epoch:1,acknowledged_stop:0});
                send({type:'audio',epoch:1,sequence:0,pcm:new Int16Array(960).fill(12000)});
                const audible=render().some(x=>x!==0);
                send({type:'stop'});
                send({type:'audio',epoch:1,sequence:1,pcm:new Int16Array(960).fill(12000)});
                const staleSilent=render().every(x=>x===0);
                send({type:'authorize',epoch:2,acknowledged_stop:0});
                const wrongApprovalLatched=sink.latched;
                send({type:'authorize',epoch:2,acknowledged_stop:1});
                send({type:'audio',epoch:2,sequence:0,pcm:new Int16Array(960).fill(12000)});
                send({type:'segment_end',epoch:2,segment:'second'});
                for(let i=0;i<17;i++)render();
                currentTime=2;
                render();
                const expiredLatched=sink.latched;
                send({type:'audio',epoch:2,sequence:1,pcm:new Int16Array(960).fill(12000)});
                return {audible,staleSilent,wrongApprovalLatched,expiredLatched,lateSilent:render().every(x=>x===0),events};
            }""",
                source,
            )
            assert all(
                results[k]
                for k in [
                    "audible",
                    "staleSilent",
                    "wrongApprovalLatched",
                    "expiredLatched",
                    "lateSilent",
                ]
            )
            assert [e["segment"] for e in results["events"] if e["type"] == "consumed"] == [
                "second"
            ]
            assert [e["epoch"] for e in results["events"] if e["type"] == "played"] == [2]
        finally:
            browser.close()


@pytest.mark.features("C1", "C2", "D1")
@pytest.mark.scenario("WORKLET-FINISH-PARTIAL-PACKET")
def test_finish_flushes_partial_capture_before_marker():
    source = Path("reachy_brain/web/static/audio-worklet.js").read_text()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            result = page.evaluate(
                """source => {
                globalThis.currentTime=0;globalThis.sampleRate=48000;
                const events=[];
                globalThis.AudioWorkletProcessor=class{constructor(){this.port={postMessage:m=>events.push(m)};}};
                globalThis.registerProcessor=(name,klass)=>{globalThis.Sink=klass;};
                (0,eval)(source);const sink=new Sink();sink.recording=true;
                sink.process([[new Float32Array(128).fill(.01)]],[[new Float32Array(128)]]);
                const before=events.length;
                sink.port.onmessage({data:{type:'finish'}});
                return {before,types:events.map(e=>e.type),samples:events[0].pcm.length,remaining:sink.input.length};
            }""",
                source,
            )
            assert result == {
                "before": 0,
                "types": ["capture", "commit"],
                "samples": 64,
                "remaining": 0,
            }
        finally:
            browser.close()


@pytest.mark.features("C1", "C9", "D1")
@pytest.mark.scenario("WORKLET-SPEECH-CAPTURE-INTERVAL")
def test_commit_interval_uses_render_clock_and_does_not_reuse_onset():
    source = Path("reachy_brain/web/static/audio-worklet.js").read_text(encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            result = page.evaluate(
                """source => {
                globalThis.currentTime=10;globalThis.sampleRate=48000;
                const events=[];
                globalThis.AudioWorkletProcessor=class{constructor(){this.port={postMessage:m=>events.push(m)};}};
                globalThis.registerProcessor=(name,klass)=>{globalThis.Sink=klass;};
                (0,eval)(source);const sink=new Sink();sink.recording=true;
                for(let i=0;i<32;i++){
                  sink.process([[new Float32Array(128).fill(.1)]],[[new Float32Array(128)]]);
                  globalThis.currentTime+=128/48000;
                }
                sink.port.onmessage({data:{type:'finish'}});
                sink.port.onmessage({data:{type:'finish'}});
                return {onset:events.find(e=>e.type==='speech_start').at,
                        commits:events.filter(e=>e.type==='commit')};
                }""",
                source,
            )
            assert result["onset"] == pytest.approx(10)
            assert result["commits"][0]["start"] == pytest.approx(10)
            assert result["commits"][0]["end"] == pytest.approx(10 + 32 * 128 / 48000)
            assert result["commits"][1]["start"] is None
        finally:
            browser.close()
