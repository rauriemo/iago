# Conducting the PC demonstration and mixed-workload soak

This is an operator procedure, not a completed run or a source of acceptance labels. SPEC.md Revision 6 and ACCEPTANCE.md control all thresholds. Use WORKLOAD_CAPTURE.md for the recorder and evaluator contracts. Keep actual activity times and outcomes; never copy this agenda into an evidence declaration as though it happened.

## Freeze the setup before starting

1. Record the branch, commit, dirty files, configuration digest, installed versions, selected Astra model, exact selected ElevenLabs voice and OpenAI fallback voice. Keep provider keys and the local control token private. Use a unique ignored evidence directory and retain command output. Record the application owner and process IDs, including the separate browser processes, for resource attribution.
2. Prepare reviewed non-secret camera/whiteboard/phone and screen tasks. Freeze expected sources, times, exact text and unreadable cases. Follow VISUAL_EVAL.md and SCREEN_EVAL.md; this workload does not replace their minimum datasets. Prepare live question-associated thumbs and wave negatives under THUMB_EVAL.md and WAVE_EVAL.md. A classifier label alone is not an accepted conversational response.
3. Configure the three isolated K1 projects and the complete 30-supported-file corpus plus 12 boundary fixtures. Freeze the independently reviewed question/passages/facts and mutation plan. Work on disposable evaluation copies; keep at least 30 supported files throughout the active interval by adding replacement fixtures before deletions. Retain original baselines and before/after hashes. Product Remove project must never delete source files.
4. Install and enable the trusted fake-calendar workflow. Configure separate local fake accounts for stdio and Streamable HTTP using EXTENDING.md. Verify both connections and their displayed account identities before measurement. Do not connect personal calendar, email or WhatsApp accounts. Define write policies before starting; record exact confirmations or applicable standing authorization for each write.
5. Prepare independent synchronized acoustic recording and the actual webcam, microphone and speakers. Follow SPEECH_OBSERVATION.md, OPENING_WORDS_EVAL.md and RESPONSE_EVAL.md. Record alignment uncertainty and device continuity. Synthetic audio or backend Stop timestamps cannot replace physical cutoff evidence. Plan both voices and a fallback attempt without changing Astra.
6. Declare the 600-second warmup before collection. Record RAM/queue/storage limits and the browser-memory capture method. Choose the representative task sequence before observing results. Keep limits fixed during the run; a reduced workload or changed cap requires its own separately identified run.

Do not start a scored run until its fixtures and recording setup are ready. Missing prerequisites are blocked. Available PC hardware does not supply independent labels or acoustic alignment automatically.

## Start synchronized collection

Start with a fresh application lifetime and launch workload capture before measured operations, so bounded timing windows have not already dropped samples. Follow WORKLOAD_CAPTURE.md to supply the local control token privately. In a new directory, run:

```powershell
uv run python -m reachy_brain.evals.workload_capture --url http://127.0.0.1:8765 --duration 5400 --interval 5 --output local-data/evidence/soak-run/observations.jsonl
```

Create the parent directory first and use a different name for every attempt. Start the independent recorder and record the alignment event. Maintain a timestamped activity log against the capture's elapsed clock. Record actual source IDs/generations, question IDs, accepted values, project revisions, operation IDs and links to retained evidence in private artifacts. The metadata capture intentionally omits many of those fields and cannot reconstruct them later.

## Ninety-minute agenda

Keep camera, screen, waves, thumbs and the complete document corpus active for the full run where possible. At least 60 actual minutes must meet all combined conditions. Record interruptions to coverage as separate windows; do not bridge outages in the declaration.

| Cadence | Required work and evidence |
| --- | --- |
| Start, then approximately every 4 minutes through minute 88 | Rotate camera, screen and document questions in that order. Record the actual submitted and completed times and supporting evidence. The maximum permitted query gap is 5 minutes; the shorter planned cadence leaves operating margin. Include current and historical visual references, crop/detail and mixed retrieval. |
| Approximately minutes 4, 12, 20, 28, 36, 44, 52, 60, 68, 76 and 84 | Perform a change/reindex batch. Distribute three edits, three moves, three deletions and three creations across all three projects during the run. Verify each stale/moved/deleted citation is retired and record search/read/status outcomes. Maintain at most 10 minutes between batches during active windows. The separate idle-desktop five-second refresh test remains required. |
| Complete cycles near minutes 4, 12, 20, 28, 36, 44, 52, 60, 68, 76 and 84 | Read, draft, then controlled-write one fake-calendar cycle. Alternate stdio and HTTP by completed cycle. Keep the same account and transport within a cycle. Retain the operation outcome and authorization evidence. Completion gaps, including run boundaries, must stay within 10 minutes; scheduled starts do not prove completion. |
| Distributed across the active workload | Obtain at least 20 valid final thumb responses to presented questions, with release/rearm and visible feedback. Keep up/down values and source/question ownership explicit. Also perform at least 20 real spoken interruptions; retain opening words and acoustic cutoff recordings. A speech-superseded thumb must not be counted as a final valid response. |
| Planned interruption trials | Include blocked tool reads/writes, credential refresh, skill-resource loading and reconciliation, following E1-PRIORITY. Use the documented fake-calendar delay/response-loss options for uncertain outcomes. Record delayed-call and reconciliation intervals overlapping interruptions. Reconcile uncertain writes instead of recreating them. Report provider cancellation separately from stopping speech. |
| Throughout and after warmup | Capture backend/child/browser resources, bounded queues and storage, source rates and loss, timing distributions and failures. Independently inspect upward oscillating memory trends and between-sample peaks. Sampling below a cap does not prove a continuous high-water bound. |

Use exact active-action UI confirmations; gestures never authorize writes. If a provider reports a plan limit, stop billable validation and record the interrupted run honestly. An incomplete operation or recording cannot be turned into a completed interval by extending timestamps. If application ownership changes, preserve the incomplete capture and begin a separately identified attempt; do not splice owners into one passing soak.

## Thirty-minute product demonstration

Run a separate 1,800-second capture using the same recorder. Demonstrate real brainstorming, Normal/Patient turns, both voices/fallback and interruption, show-then-lower and historical visual retrieval, pins/crops/evidence, live thumbs and waves with restrained initiative, simultaneous camera/screen sharing, project search/citations and controls, notes/export/delete, diagnostics and the fake-calendar workflow. Record permission/device-loss and clearing/recovery behavior against the relevant cases. The 90-minute coverage evaluator is not a 30-minute demonstration scorer; do not pad the demonstration to satisfy its schema. Preserve a timestamped scenario-to-evidence index and user assessment of usefulness.

## Evaluate and review the evidence

Build workload-activities.json from the actual log using the WorkloadRun schema. Set reported-real only for a real run, use measured windows/times and link each activity to reviewable records. Preserve missed targets rather than editing times to fit the agenda. Run each command separately and retain its exit code and output:

```powershell
uv run python -m reachy_brain.evals.workload_coverage --input local-data/evidence/soak-run/workload-activities.json --output local-data/evidence/soak-run/coverage.json
uv run python -m reachy_brain.evals.workload_resources --input local-data/evidence/soak-run/observations.jsonl --output local-data/evidence/soak-run/resources.json
uv run python -m reachy_brain.evals.workload_timings --input local-data/evidence/soak-run/observations.jsonl --output local-data/evidence/soak-run/timings.json
```

These commands assess declared coverage or limited observations; none establishes full acceptance. Independently bind activity records, final accepted turns, corpus mutations and calendar outcomes to actual artifacts. Supply the separate optical, acoustic, response-latency and human-reviewed K1 reports. Report count/median/p95/slowest for every required timing family and preserve missing measurements as blocked. Physical Stop p95 remains below 150 ms, spoken interruption p95 below 300 ms, ordinary response median below 3 seconds/p95 below 5 seconds, and combined retrieval uses its configured 10-second deadline.

Finish with an evidence index listing each SPEC/ACCEPTANCE gate, exact commands, fixture/configuration hashes, actual outcomes, report paths and remaining prerequisites. Keep raw recordings distinguishable from generated summaries. This PC procedure does not qualify Reachy camera timing, motion, Wi-Fi loss, local stop or onboard resources; use ROBOT_EVAL.md and the original robot gates on the physical device.

When recording each calendar cycle, include its actual module and account on every read, draft and write activity, alongside cycle and transport. All three namespace values must match the same configured connection within that cycle. Preserve actual returned operation and authorization evidence; a timing sample or manually entered identity alone does not prove completion.


After independent review, bind the capture, activity log and supporting records using ReviewedWorkloadBundle, then run the live-PC artifact entrypoint in WORKLOAD_CAPTURE.md. Keep its outcome separate from physical qualification: even a passing artifact check retains review_required and false physical/acceptance flags. Missing or incomplete records remain blocked, and failed component evidence remains failed.
