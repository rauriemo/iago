# Recorded physical cutoff evaluation

Run `uv run --extra vision --extra robot iago-verify --suite live-pc --select physical_acoustics --output local-data/evidence/physical-cutoff` after privately setting `IAGO_PC_ACOUSTIC_MANIFEST` to a recording manifest. Without that manifest the check is blocked. No recordings are captured automatically by this scorer. Its result applies to the declared recording environment and application revision, not automatically to the current application or all physical acceptance gates.

Use an independent microphone near the actual speakers or a qualified independent hardware loopback path. Document what the path measures: loopback alone does not prove downstream speaker silence. Review the selected channel to ensure user speech, clicks and background sound cannot be mistaken for assistant output. Do not provide browser cancellation timestamps or generated PCM as physical evidence. Record the Stop actuation or start of the spoken interruption on an aligned independent measurement timeline, with a measured uncertainty bound. A channel/event annotation requires human review. Make the assistant utterance continue beyond the intended interruption; a natural utterance ending cannot qualify a Stop trial.

Keep recordings and manifests under ignored local-data, not Git. Freeze file hashes, event annotations and calibrated thresholds before scoring. Use separate calibration material; do not tune thresholds to make failed cutoffs pass. Preserve original files and record corrections separately. WAV inputs must be uncompressed signed 16-bit PCM, 8–96 kHz, 1–8 channels, at most 30 seconds/32 MiB per file. Each trial needs a quarter-second noise calibration region, 100 ms of assistant output immediately before the event and at least two seconds after it. The recording must finish with at least half a second below threshold. Background RMS must be at least 12 dB below the fixed detection threshold. These bounds validate measurement usability, not source identity; the operator must establish isolation and physical provenance.

The scorer finds the last 5 ms window at or above the calibrated RMS threshold after the event, includes resumed output and adds alignment uncertainty to its upper latency estimate. It reports median, nearest-rank p95, slowest and counts separately for OpenAI, ElevenLabs and fallback, and for Stop versus spoken interruption. Targets remain strictly below 150 ms and 300 ms respectively. At least 20 spoken interruptions overall and at least one recording in each provider/action group are required by this cutoff selector. Small groups are reported as such and do not establish general latency distributions. Short recordings do not prove absence of later replay outside their observation window.

Manifest structure (replace every example value with actual reviewed evidence):

```json
{
  "fixture_kind": "physical-pc-independent-recording",
  "operator": "reviewer identifier",
  "recorded_at": "ISO date/time of recording",
  "application_revision": "commit plus retained dirty-source fingerprint",
  "devices": {"speaker": "actual device", "recorder": "actual device and driver"},
  "voice_ids": {"openai": "configured voice", "elevenlabs": "exact configured voice ID"},
  "trials": [{
    "id": "unique-trial-id", "file": "relative-recording.wav",
    "sha256": "64 lowercase hex characters from that recording",
    "provider": "openai", "action": "stop", "channel": 0,
    "event_sample": 48000, "uncertainty_ms": 2.0,
    "noise_start_sample": 0, "noise_end_sample": 12000,
    "threshold_dbfs": -40.0,
    "independent_recording": true, "human_reviewed_isolation": true,
    "instrument": "independent recorder and calibration reference",
    "event_alignment": "how event sample and uncertainty were independently established"
  }]
}
```

Use `provider` values `openai`, `elevenlabs`, `fallback` and `action` values `stop`, `spoken`. Each recording event may occur only once; reusing a waveform/event as a new provider trial is rejected. Threshold and alignment values above are illustrative, not calibrated defaults. Metadata is operator supplied and recorded as such; the software cannot authenticate a physical recording's provenance merely from a manifest.

The complete acceptance contract still requires ten minutes of speaker output with zero self-triggered turns, opening-word preservation, both voice paths and fallback, ordinary response latency, integration-blocked interruption trials, the 30-minute product demonstration and 90-minute mixed soak. Separate robot recordings/qualification are still needed. This selector does not pass any of those other requirements or the full physical-speech gate.


## E1 interruption during blocked integration activity

Run `uv run --extra vision --extra robot iago-verify --suite live-pc --select physical_integration_acoustics --output local-data/evidence/physical-integration-cutoff` with private `IAGO_PC_INTEGRATION_ACOUSTIC_MANIFEST` pointing to the additional manifest. This selector is independently blocked without its recordings. It does not replace the original physical acoustic selector, deterministic E1 priority tests, robot qualification or the mixed soak.

Use the original manifest metadata and acoustic trial fields above, changing fixture_kind to `physical-pc-independent-integration-recording`. Each trial additionally requires:

```json
{
  "activity": "mcp_read",
  "activity_started_sample": 24000,
  "activity_released_sample": 96000,
  "activity_trace_file": "relative-redacted-backend-trace.json",
  "activity_trace_sha256": "64 lowercase hex characters from the retained trace",
  "human_reviewed_activity": true,
  "activity_alignment": "Reviewed correlation of the blocked backend interval to recording sample indices"
}
```

Illustrative indices are not usable calibration. Record actual blocked `mcp_read`, `mcp_write`, `credential_refresh`, `skill_load` and `reconciliation` work, each during OpenAI, chosen ElevenLabs and fallback output, with both Stop and spoken interruptions. All 30 groups need evidence; require at least 20 spoken trials overall. At the minimum this means 35 distinct recorded events. The scorer reports count, median, nearest-rank p95 and slowest for each group with unchanged strict <150 ms Stop / <300 ms spoken p95 targets. A waveform/event reused under a different ID, voice, activity or channel cannot supply another trial. Small groups remain small-set evidence.

Retain a redacted trace (nonempty, at most 1 MiB) proving the named actual operation was blocked, with dispatch/hold/release observations and the operation ID where applicable. Credential refresh traces must omit token values. Review how trace observations align with the independent recording and include that alignment error in uncertainty_ms. Annotate activity_started_sample as the established start of blocking, not merely request submission. The activity must begin before the earliest possible interruption and remain blocked through the measured cutoff plus a half-second silence tail; its release must lie within the recording. Use bounded test delays and local fake integrations; do not connect production personal accounts. Stop speech must remain independent of the held operation.

Trace hashes bind retained bytes; the scorer cannot authenticate hardware provenance or infer backend blocking from arbitrary trace text. The human reviewer must establish those facts and correlation. Missing or invalid traces, unreviewed activity, inadequate overlap and incomplete coverage cannot pass. Evidence reports retain hashes, sample intervals, operator metadata and per-group measurements, without copying trace contents or recordings. A pass applies only to the reviewed captured PC trials; it proves neither later behavior outside the recording nor full E1 acceptance.
