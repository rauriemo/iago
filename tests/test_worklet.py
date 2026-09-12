"""Run the shipped sink code in Chromium with a deterministic synthetic render clock."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


@pytest.mark.features("C2", "D5")
@pytest.mark.scenario("WORKLET-QUEUE-ITEM-BOUND")
def test_marker_and_tiny_audio_floods_are_bounded_and_malformed_markers_stop():
    source = Path("reachy_brain/web/static/audio-worklet.js").read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            result = browser.new_page().evaluate(
                """source => {
                globalThis.currentTime=0;globalThis.sampleRate=48000;
                globalThis.AudioWorkletProcessor=class {constructor(){this.port={postMessage:m=>this.events.push(m)};this.events=[];}};
                globalThis.registerProcessor=(_,klass)=>{globalThis.Sink=klass;};
                eval(source);
                const checks=[];
                for(const kind of ['segment_end','audio']){
                    const sink=new Sink(),send=m=>sink.port.onmessage({data:m});
                    send({type:'authorize',epoch:1,acknowledged_stop:0});
                    for(let i=0;i<1024;i++)send({type:kind,epoch:1,sequence:i,segment:'s'+i,pcm:new Int16Array([1])});
                    const atLimit=sink.queue.length===1024&&!sink.latched;
                    send({type:kind,epoch:1,sequence:1024,segment:'overflow',pcm:new Int16Array([1])});
                    const out=new Float32Array(128);sink.process([],[[out]]);
                    checks.push(atLimit&&sink.latched&&sink.queue.length===0&&sink.buffered===0&&out.every(x=>x===0)
                        &&sink.events.filter(e=>e.type==='overflow').length===1
                        &&!sink.events.some(e=>e.type==='consumed'||e.type==='played'));
                }
                for(const segment of ['',null,3,'s'.repeat(129)]){
                    const sink=new Sink(),send=m=>sink.port.onmessage({data:m});
                    send({type:'authorize',epoch:1,acknowledged_stop:0});
                    send({type:'segment_end',epoch:1,segment});
                    const out=new Float32Array(128);sink.process([],[[out]]);
                    checks.push(sink.latched&&sink.queue.length===0&&out.every(x=>x===0)
                        &&sink.events.filter(e=>e.type==='invalid_audio').length===1);
                }
                return checks;
                }""",
                source,
            )
            assert len(result) == 6 and all(result), result
        finally:
            browser.close()


@pytest.mark.features("C2", "C8", "D5")
@pytest.mark.scenario("WORKLET-EPOCH-QUEUE-ISOLATION")
def test_new_epoch_cannot_play_or_acknowledge_previous_epoch_queue():
    source = Path("reachy_brain/web/static/audio-worklet.js").read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            result = browser.new_page().evaluate(
                """source => {
                globalThis.currentTime=0;globalThis.sampleRate=48000;
                const events=[];
                globalThis.AudioWorkletProcessor=class {constructor(){this.port={postMessage:m=>events.push(m)};}};
                globalThis.registerProcessor=(_,klass)=>{globalThis.Sink=klass;};
                eval(source);const sink=new Sink(),send=m=>sink.port.onmessage({data:m});
                send({type:'authorize',epoch:1,acknowledged_stop:0});
                send({type:'audio',epoch:1,sequence:0,pcm:new Int16Array(240).fill(16000)});
                send({type:'segment_end',epoch:1,segment:'old'});
                send({type:'authorize',epoch:2,acknowledged_stop:99});
                const rejectedPreserved=sink.epoch===1&&sink.buffered===480&&sink.queue.length===2;
                send({type:'authorize',epoch:2,acknowledged_stop:0});
                const cleared=sink.buffered===0&&sink.queue.length===0;
                send({type:'audio',epoch:1,sequence:1,pcm:new Int16Array(240).fill(16000)});
                send({type:'segment_end',epoch:1,segment:'late-old'});
                send({type:'audio',epoch:2,sequence:0,pcm:new Int16Array(240).fill(-16000)});
                send({type:'segment_end',epoch:2,segment:'new'});
                const samples=[];
                for(let i=0;i<9;i++){
                    const out=new Float32Array(128);sink.process([],[[out]]);
                    samples.push(...out);currentTime+=128/48000;
                }
                return {rejectedPreserved,cleared,positive:samples.some(x=>x>0),
                    negative:samples.some(x=>x<0),events};
                }""",
                source,
            )
            assert result["rejectedPreserved"]
            assert result["cleared"]
            assert result["negative"] and not result["positive"]
            assert [e for e in result["events"] if e["type"] == "consumed"] == [
                {"type": "consumed", "epoch": 2, "segment": "new"}
            ]
            assert [e for e in result["events"] if e["type"] == "played"] == [
                {"type": "played", "epoch": 2, "sequence": 0}
            ]
        finally:
            browser.close()


@pytest.mark.features("C2", "D5")
@pytest.mark.scenario("WORKLET-PREALLOCATION-AUDIO-BOUND")
def test_oversize_and_malformed_pcm_stop_before_resampled_allocation():
    source = Path("reachy_brain/web/static/audio-worklet.js").read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            result = page.evaluate(
                """source => {
              globalThis.currentTime=0;globalThis.sampleRate=48000;
              const events=[], allocations=[];
              globalThis.AudioWorkletProcessor=class {constructor(){this.port={postMessage:m=>events.push(m)};}};
              globalThis.registerProcessor=(_,klass)=>{globalThis.Sink=klass;};
              const Original=Float32Array;
              globalThis.Float32Array=class extends Original {constructor(n){super(n);allocations.push(n);}};
              eval(source);const sink=new Sink(),send=m=>sink.port.onmessage({data:m});
              send({type:'authorize',epoch:1,acknowledged_stop:0});
              send({type:'audio',epoch:1,sequence:0,pcm:new Int16Array(240001)});
              const oversize=allocations.length===0&&sink.latched&&sink.buffered===0;
              send({type:'authorize',epoch:2,acknowledged_stop:1});
              send({type:'audio',epoch:2,sequence:0,pcm:new Int16Array(144000)});
              const first=allocations.length===1&&sink.buffered===288000;
              send({type:'audio',epoch:2,sequence:1,pcm:new Int16Array(144000)});
              const cumulative=allocations.length===1&&sink.latched&&sink.buffered===0&&sink.queue.length===0;
              send({type:'authorize',epoch:3,acknowledged_stop:2});
              send({type:'audio',epoch:3,sequence:0,pcm:[1,2,3]});
              const invalid=allocations.length===1&&sink.latched;
              globalThis.Float32Array=Original;
              return {oversize,first,cumulative,invalid,overflow:events.filter(e=>e.type==='overflow').length,
                      malformed:events.filter(e=>e.type==='invalid_audio').length};
            }""",
                source,
            )
            assert all(result[k] for k in ("oversize", "first", "cumulative", "invalid"))
            assert result["overflow"] == 2 and result["malformed"] == 1
        finally:
            browser.close()


@pytest.mark.features("C2", "C8", "D5")
@pytest.mark.scenario("WORKLET-SETTINGS-OWNERSHIP-ISOLATION")
def test_settings_cannot_reauthorize_playback_or_corrupt_capture_state():
    source = Path("reachy_brain/web/static/audio-worklet.js").read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            result = page.evaluate(
                """source => {
              globalThis.currentTime=0;globalThis.sampleRate=48000;
              globalThis.AudioWorkletProcessor=class {constructor(){this.port={postMessage:()=>{}};}};
              globalThis.registerProcessor=(_,klass)=>{globalThis.Sink=klass;};
              eval(source);const sink=new Sink();
              const send=m=>sink.port.onmessage({data:m});
              send({type:'settings',recording:true,muted:false,patient:true,volume:.5,
                latched:false,epoch:99,stopGeneration:-1,queue:[{samples:[1]}],threshold:0});
              const protectedState=sink.latched&&sink.epoch===-1&&sink.stopGeneration===0&&sink.queue.length===0&&sink.threshold===.018;
              const valid=sink.recording&&sink.patient&&!sink.muted&&sink.volume===.5;
              sink.input=[1,2];sink.speaking=true;
              send({type:'settings',volume:.3});
              const partial=sink.input.length===2&&sink.speaking;
              for(const bad of [NaN,Infinity,-1,2,'0.5'])send({type:'settings',volume:bad,recording:false});
              send({type:'settings',muted:'false',volume:.9});
              const atomic=sink.recording&&sink.volume===.3&&sink.input.length===2;
              send({type:'settings',muted:true});
              const cleared=sink.muted&&sink.input.length===0&&!sink.speaking;
              send({type:'audio',epoch:99,sequence:0,pcm:new Int16Array(480).fill(10000)});
              const output=new Float32Array(128);sink.process([],[[output]]);
              return {protectedState,valid,partial,atomic,cleared,silent:output.every(v=>v===0)};
            }""",
                source,
            )
            assert all(result.values()), result
        finally:
            browser.close()


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
