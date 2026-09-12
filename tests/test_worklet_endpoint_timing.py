"""Shipped worklet with synthetic PCM and render clock; no physical endpoint claim."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


@pytest.mark.features("C1", "C3")
@pytest.mark.scenario("WORKLET-NORMAL-PATIENT-ENDPOINT-TIMING")
@pytest.mark.parametrize("rate", [44100, 48000])
@pytest.mark.parametrize("patient", [False, True])
@pytest.mark.parametrize("continuation", [False, True])
def test_silence_deadline_and_resumed_speech(rate, patient, continuation, record_property):
    source = Path("reachy_brain/web/static/audio-worklet.js").read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            result = browser.new_page().evaluate(
                """({source,rate,patient,continuation}) => {
                    globalThis.sampleRate=rate;globalThis.currentTime=0;
                    const events=[];let processed=0;
                    globalThis.AudioWorkletProcessor=class {constructor(){
                        this.port={postMessage:m=>events.push(m)};
                    }};
                    globalThis.registerProcessor=(_,klass)=>{globalThis.Sink=klass;};
                    (0,eval)(source);
                    const sink=new Sink(),send=m=>sink.port.onmessage({data:m});
                    send({type:'settings',recording:true,muted:false,patient});
                    const render=(blocks,amplitude)=>{
                        for(let n=0;n<blocks;n++){
                            globalThis.currentTime=processed/rate;
                            sink.process([[new Float32Array(128).fill(amplitude)]],[[new Float32Array(128)]]);
                            processed+=128;globalThis.currentTime=processed/rate;
                        }
                    };
                    const threshold=patient?1.2:.7;
                    render(40,.1);
                    if(continuation){render(Math.floor((threshold-.15)*rate/128),0);render(40,.1);}
                    const premature=events.filter(e=>e.type==='commit').length;
                    const speechEnd=processed/rate;
                    render(Math.ceil((threshold+.1)*rate/128),0);
                    const commits=events.filter(e=>e.type==='commit');
                    const onsets=events.filter(e=>e.type==='speech_start');
                    return {premature,commits:commits.map(e=>({start:e.start,end:e.end})),
                        onsets:onsets.length,silence_seconds:commits[0]?.end-speechEnd,
                        source_samples:processed,capture_samples:events.filter(e=>e.type==='capture')
                          .reduce((n,e)=>n+e.pcm.length,0)+sink.input.length};
                }""",
                {"source": source, "rate": rate, "patient": patient, "continuation": continuation},
            )
        finally:
            browser.close()
    threshold = 1.2 if patient else 0.7
    assert result["premature"] == 0
    assert len(result["commits"]) == result["onsets"] == 1
    assert threshold - 1e-9 <= result["silence_seconds"] <= threshold + 128 / rate + 1e-9
    assert result["commits"][0]["start"] == pytest.approx(0, abs=1e-9)
    assert abs(result["capture_samples"] - result["source_samples"] * 24000 / rate) <= 1
    record_property(
        "measurements",
        {
            "synthetic": True,
            "input_rate": rate,
            "patient": patient,
            "continuation": continuation,
            "threshold_seconds": threshold,
            "render_quantum_seconds": 128 / rate,
            **result,
        },
    )


@pytest.mark.features("C1", "C3")
@pytest.mark.scenario("WORKLET-PATIENT-MANUAL-ENDPOINT")
def test_manual_commit_does_not_wait_for_patient_deadline_or_duplicate_after_silence():
    source = Path("reachy_brain/web/static/audio-worklet.js").read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            result = browser.new_page().evaluate(
                """source => {
                globalThis.currentTime=0;globalThis.sampleRate=48000;const events=[];
                globalThis.AudioWorkletProcessor=class{constructor(){this.port={postMessage:m=>events.push(m)};}};
                globalThis.registerProcessor=(_,klass)=>{globalThis.Sink=klass;};(0,eval)(source);
                const sink=new Sink();sink.port.onmessage({data:{type:'settings',recording:true,patient:true}});
                for(let i=0;i<40;i++){globalThis.currentTime=i*128/48000;
                    sink.process([[new Float32Array(128).fill(.1)]],[[new Float32Array(128)]]);}
                globalThis.currentTime=40*128/48000;
                sink.port.onmessage({data:{type:'finish'}});
                const immediate=events.filter(e=>e.type==='commit').length;
                for(let i=40;i<700;i++){globalThis.currentTime=i*128/48000;
                    sink.process([[new Float32Array(128)]],[[new Float32Array(128)]]);}
                return {immediate,commits:events.filter(e=>e.type==='commit').map(e=>e.end)};
            }""",
                source,
            )
        finally:
            browser.close()
    assert result["immediate"] == 1
    assert result["commits"] == [pytest.approx(40 * 128 / 48000)]


@pytest.mark.features("C1", "C3", "D2", "D3")
@pytest.mark.scenario("EDGE-NORMAL-PATIENT-ENDPOINT-TIMING")
@pytest.mark.parametrize("patient", [False, True])
@pytest.mark.parametrize("continuation", [False, True])
def test_edge_runtime_settings_drive_local_endpoint_timing(patient, continuation, record_property):
    import numpy as np
    from test_edge_runtime import Media

    from reachy_brain.robot.runtime import EdgeRuntime

    runtime = EdgeRuntime(Media())
    events, blocks = [], 0
    try:
        runtime.configure_audio(False, patient, 0.8)

        def render(count, amplitude):
            nonlocal blocks
            for _ in range(count):
                events.extend(
                    runtime.supervisor.microphone(
                        np.full(320, amplitude, dtype=np.float32),
                        16000,
                        captured=blocks * 0.02,
                        sequence=blocks,
                    )
                )
                blocks += 1

        threshold = 1.2 if patient else 0.7
        render(6, 0.1)
        if continuation:
            render(int((threshold - 0.15) / 0.02), 0)
            render(6, 0.1)
        assert not any(e["type"] == "speech_end" for e in events)
        speech_end = blocks * 0.02
        render(int((threshold + 0.1) / 0.02), 0)
        ends = [e for e in events if e["type"] == "speech_end"]
        assert len(ends) == len([e for e in events if e["type"] == "speech_start"]) == 1
        elapsed = ends[0]["captured"] - speech_end
        assert threshold - 1e-9 <= elapsed <= threshold + 0.02 + 1e-9
        record_property(
            "measurements",
            {
                "synthetic": True,
                "patient": patient,
                "continuation": continuation,
                "silence_seconds": elapsed,
                "input_block_seconds": 0.02,
            },
        )
    finally:
        runtime.close()
    assert all(not thread.is_alive() for thread in runtime.threads)
