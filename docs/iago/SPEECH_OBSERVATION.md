# Speech observation coverage

Devices & session details diagnostics also include `control_delivery`: page-lifetime speech-start and stop attempts, successful socket sends, unavailable sockets and send failures, with a page identity. Stop includes manual and local guard stops. These counters retain no payload and complement backend activity; successful `socket.send` is not a backend acknowledgment. Events lost before delivery from the audio worklet to the page remain outside this counter's coverage. Preserve both browser diagnostics and backend exports when investigating incomplete delivery.

Use Export speech activity before and after an echo observation. Save both files privately, then compare them:

```powershell
uv run python -m reachy_brain.evals.speech_observation --start local-data/echo/start.json --end local-data/echo/end.json --output local-data/echo/interval.json
```

The comparator requires the same controller owner, forward elapsed time, consistent counters and contiguous sequence records for the entire interval. It rejects dropped interval events, altered overlapping samples and owner recreation. Drops strictly before the start export are allowed. Export more frequently if activity can exceed 512 records; never interpret a reset or incomplete record as zero events. Inputs are limited to 512 KiB each and an existing output cannot be overwritten.

The report contains separate onset, uncertain-onset and accepted-speech counts, with event times and epochs. It measures backend observation duration only. A successful command means the available backend interval is complete, not that an echo test passed. Even zero counts could reflect a disconnected microphone. Independent recordings/review must establish enabled microphone continuity, actual assistant playback duration and which notifications came from a real user, playback or another source. Browser-local events before delivery to the controller remain outside this export's coverage.

ACCEPTANCE.md still requires ten minutes of assistant output with zero self-triggered turns, at least 20 spoken interruptions, preserved opening words and physical cutoff measurements for both voices/fallback. This comparator supplies one evidence component. Actual echo capture and its independent physical qualification remain unfinished; `physical_echo_validated` is always false here.


## Reviewed recording check

The live-PC selector now accepts a private reviewed bundle:

```powershell
$env:IAGO_PC_ECHO_FIXTURES='local-data/echo'
uv run --extra vision --extra robot iago-verify --suite live-pc --select test_reviewed_ten_minute_echo_observation --output local-data/evidence/echo-reviewed
```

Supply `capture.json`, `review.json`, `start.json`, `end.json` and the independent WAV named by the capture. Export the actual backend snapshots around the observation. Record independent assistant output and live microphone input on distinct synchronized channels, preserving evidence that capture/VAD remained enabled and delivery remained connected. Backend counters alone cannot establish microphone continuity. Save browser control-delivery diagnostics, device setup and alignment procedure for independent review. Keep recordings and personal data out of Git.

`EchoCapture` in `reachy_brain/evals/echo_observation.py` defines the strict capture schema: version 1, physical-PC fixture kind, relative WAV filename and SHA-256, both snapshot digests, application revision, device description, distinct playback/microphone channel indices, voice IDs for OpenAI/ElevenLabs/fallback, recording sample-zero position on the backend elapsed clock, alignment uncertainty and reviewed playback sample intervals with provider labels. Use the module's imported `digest` function on parsed models for snapshot/capture bindings, and SHA-256 of exact bytes for the WAV. The recording must cover the entire backend interval including alignment uncertainty. All playback spans must be ordered, disjoint and inside that interval. Include at least 600 seconds of actual reviewed assistant output overall, with both voices and fallback represented. This does not set a new per-provider duration threshold.

WAV limits: uncompressed PCM16, 8–96 kHz, 2–8 channels, 600–7,200 seconds, at most 512 MiB. JSON files are capped at 512 KiB each. Audio hashing and inspection use bounded blocks. A nonzero audio sample only rejects an entirely silent playback span; it does not identify a voice or prove audible speech. The independent human review must verify the full declared spans actually contain assistant playback, including natural pauses, and must not count idle time as output.

`EchoReview` binds the parsed capture digest and requires reviewer identity, independent review, verified recording origin, microphone continuity, playback intervals and clock alignment, plus the supporting continuity-evidence description. Classify every backend event sequence as `user`, `assistant_echo`, `other` or `unknown`, including onsets that produced no saved transcript. Do not fill declarations without doing the review. Unknown events prevent a passing check; assistant-echo accepted turns fail the zero-self-trigger gate. Echo onset counts are reported separately, because an onset can interrupt output without creating a new turn. Investigate other-origin events separately; the result is limited to assistant echo.

The scorer checks file hashes, PCM bounds, interval coverage, activity continuity and complete review bindings. It reports reviewed duration, echo notifications and self-triggered turns. It cannot authenticate a reviewer or establish recording origin from declarations alone. A passing reviewed-record check does not qualify physical cutoff, opening words, the 20 required interruptions, general echo reliability or full V1. Missing evidence remains blocked. Actual recording collection and independent human review are still required; synthetic unit-test bundles are not physical evidence.


Echo recording integrity: the scorer streams the WAV into a private temporary file while hashing it, then measures that same snapshot. The original may not change the measurement after verification. This uses at most 512 MiB of temporary disk per evaluation, with bounded copy/PCM buffers; the temporary file closes on normal completion or error. Ensure sufficient temporary storage; an I/O failure is a failed evaluation, not a pass. Reported fixture_kind distinguishes PC and robot recording declarations, without authenticating their origin.
