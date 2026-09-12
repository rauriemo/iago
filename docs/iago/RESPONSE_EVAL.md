# Physical response latency

This scorer measures independently recorded assistant sound relative to the end of the user's utterance. Backend response diagnostics and first audio dispatch are different measurements and cannot pass this gate. This guide adds no physical evidence by itself. The ordinary-turn targets in ACCEPTANCE.md remain median below 3 seconds and p95 below 5 seconds; retrieval is reported separately.

Keep private recordings and manifests outside Git, for example under ignored local-data. Run:

```powershell
$env:IAGO_PC_RESPONSE_MANIFEST = 'C:\private-fixtures\response\manifest.json'
uv run --extra vision --extra robot iago-verify --suite live-pc --select physical_response_latency --output local-data/evidence/physical-response
```

An absent manifest is blocked and returns non-success. Malformed or insufficient evidence fails. This one check does not qualify the full physical suite, the robot, interruption cutoff, echo rejection, response usefulness or the mixed-workload soak.

Use a separate recorder that captures actual PC speaker output. Preserve a synchronized user-microphone channel or other independently reviewed endpoint reference. The channel scored here must isolate assistant output: a digital pre-speaker stream alone does not establish physical audibility. Review the recording to establish the user's actual final speech sample, not the application's endpoint/commit acknowledgment. Declare alignment uncertainty in milliseconds; the scorer adds it conservatively. Explain channel isolation and synchronization in instrument/event_alignment. Preserve supporting recordings and review notes for inspection.

Before scoring, freeze IDs, workload labels, endpoints, calibration intervals and thresholds. Human review must establish that the first detected assistant energy belongs to the answer for that utterance, with no notification click, user leakage, unrelated sound or earlier assistant answer. It must also establish that ordinary trials contain no retrieval. The scorer detects energy rather than recognizing speech or authenticating these declarations. A checked declaration or file hash cannot prove independent capture or human review. Do not mark synthetic PCM or a replayed provider response as physical evidence.

Each trial is an uncompressed signed 16-bit PCM WAV, 8–96 kHz, one to eight channels, at most 30 seconds and 32 MiB. Files must stay inside the manifest's directory. SHA256 binds the same bounded bytes that are decoded. Choose a noise interval of at least 250 ms ending at least 250 ms before the speech endpoint. Its RMS must remain below one quarter of the fixed amplitude threshold. The isolated assistant channel must have no above-threshold energy in the final 250 ms before that endpoint. Include at least five seconds after the endpoint; a missing reply fails rather than scoring zero. Record long enough to capture slow responses too; do not omit failed or slow trials.

The detector examines consecutive 5 ms RMS windows from the endpoint. Latency uses the end of the first above-threshold window plus alignment uncertainty, so the window estimate is conservative. Report median, nearest-rank p95, slowest and sample count per provider/workload. Ordinary OpenAI, chosen ElevenLabs and an actual fallback attempt must each have measurements; all ordinary groups must meet both strict targets. Retrieval groups have no ordinary-turn pass flag; their backend retrieval deadline and other acceptance gates remain separate. The acceptance contract does not set an ordinary-response sample minimum. This scorer reports actual counts without inventing one: a one-trial group is plainly weak evidence and does not establish general latency. Choose and disclose a representative frozen sample plan before the physical run, including the required loaded-workload runs.

Manifest structure (replace every placeholder with reviewed real evidence):

```json
{
  "fixture_kind": "physical-pc-independent-recording",
  "operator": "operator name",
  "recorded_at": "ISO timestamp",
  "application_revision": "commit and dirty patch identity",
  "devices": {"speakers": "actual model", "recorder": "actual model"},
  "voice_ids": {"openai": "configured voice", "elevenlabs": "chosen exact voice ID"},
  "trials": [{
    "id": "unique trial ID",
    "file": "trial.wav",
    "sha256": "64 lowercase hexadecimal characters",
    "provider": "openai",
    "workload": "ordinary",
    "channel": 0,
    "speech_end_sample": 16000,
    "uncertainty_ms": 2.0,
    "noise_start_sample": 0,
    "noise_end_sample": 8000,
    "threshold_dbfs": -40.0,
    "independent_recording": true,
    "human_reviewed_isolation": true,
    "human_reviewed_turn": true,
    "instrument": "reviewed physical capture and isolation method",
    "event_alignment": "reviewed user endpoint and synchronization method"
  }]
}
```

Providers are openai, elevenlabs or fallback; workloads ordinary or retrieval. Fallback means a real preferred-provider failure followed by fallback output, not relabeling an ordinary OpenAI trial. Declare the observed provider and retain the supporting run trace. IDs and (recording hash, endpoint) pairs must be unique even across channels. At most 1,000 trials are admitted. Results retain partial measured evidence if a later recording fails, and never count the missing trial as a pass. Keep the recordings separate from summaries and redact personal metadata before sharing evidence reports.


The physical response entrypoint reads and validates one bounded manifest snapshot (1 MiB maximum) and records its exact SHA256 with the measurements. A prior filesystem size observation cannot authorize a larger replacement read. This binds the scored manifest bytes without establishing their physical authenticity.
