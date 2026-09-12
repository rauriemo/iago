# Runtime capture for the demonstration and soak

The required 30-minute product demonstration and 90-minute mixed workload remain unqualified. Use [WORKLOAD_RUNBOOK.md](WORKLOAD_RUNBOOK.md) for setup, the timed operator agenda and evidence review. This recorder collects supporting runtime observations; it neither drives the required activities nor scores a run as passing.

Start the application normally. In a separate terminal, configure `IAGO_LOCAL_CONTROL_TOKEN` privately with the local capability token from the application's authorized browser URL fragment. This is the local application token, not an OpenAI, ElevenLabs or HF provider credential. Do not include it in command-line arguments, captured output or Git. Close that terminal or remove the environment value afterward.

From the repository root, choose a new output filename inside ignored `local-data/evidence`:

```powershell
uv run python -m reachy_brain.evals.workload_capture --url http://127.0.0.1:8765 --duration 5400 --interval 5 --output local-data/evidence/workload-observations.jsonl
```

Use `--duration 1800` for the demonstration. The parent output directory must already exist. Existing files are never overwritten. The recorder accepts only the loopback HTTP origin, follows no redirects and applies a five-second request timeout, 128-KiB response/line limit and 32-MiB output cap. Application restart/owner change or a failed request ends the capture as incomplete. A partial file lacking a completion footer is also incomplete. A `complete_capture` footer means the requested observation interval finished, never that acceptance passed.

The authenticated `/api/workload-status` endpoint reports application owner/elapsed time, mode, source IDs/kinds/generations, browser upload admission counts, retained visual frame/storage totals, numeric resource/perception health, thumb enable/retained-acceptance count, speech activity counters and pending integration/confirmation counts. It excludes titles, image pixels, transcript content, document paths, tool payloads and credentials.

Counts have deliberately limited meanings: retained thumbs are a bounded current-controller deque, not cumulative qualified gestures; retained frames are current storage, not proof of continuous capture; browser upload counts are not robot camera throughput; pending integrations do not count completed calendar cycles. Resource samples can be stale/unavailable and their age/status must be retained. Speech activity does not classify echo or measure acoustic cutoff.

Complete all activities and gates in ACCEPTANCE.md alongside this capture: camera/screen/perception coverage, document corpus/mutations and query/reindex cadence, valid thumb responses and speech interruptions, alternating fake-calendar cycles, delays/reconciliation, both voices/fallback and independent physical measurements. Preserve separate timestamped activity evidence and frozen/reviewed datasets. A generic Idle recording or synthetic run cannot replace any of them. The full workload driver and reviewed physical evidence remain outstanding. The declared-activity coverage checker below is available; it does not authenticate or grade physical evidence.


## Declared activity coverage checker

Record a separate activity JSON using `WorkloadRun` in `reachy_brain/evals/workload_coverage.py`. Its bounded schema accepts version 1, origin (`synthetic` or `reported-real`), duration in seconds, active windows and activities. Each active window records start/end, camera/screen/wave/thumb enable coverage and supported-document count. Every activity records a unique ID, relative timestamp, kind and an evidence reference; mutations name their project. Calendar read/draft/write activities name the same cycle and transport. Delayed-call/reconciliation activities include an end timestamp to test overlap with interruption events. References must point to retained, independently assessable records; writing a declaration does not prove the action occurred.

```powershell
uv run python -m reachy_brain.evals.workload_coverage --input local-data/evidence/workload-activities.json --output local-data/evidence/workload-coverage.json
```

The input is capped at 4 MiB, 1,000 windows and 10,000 events. The report binds its exact input SHA256 and never overwrites an existing report. Exit zero means declared activity coverage meets this check, not full acceptance. Missing coverage or invalid input returns non-success. `physical_qualification` and `acceptance_pass` always remain false.

The checker requires 5,400 seconds total, 3,600 seconds of combined coverage without double-counting overlapping windows, 30 supported files, camera/screen/document query rotation with at most 300-second gaps, reindex at most 600 seconds apart within the active windows, three mutations of each required type across at least three projects, 20 thumb events and 20 interruptions. Complete calendar read/draft/write cycles must stay within a 600-second completion cadence across the run and alternate stdio/Streamable HTTP. Both delayed calls and reconciliation must overlap declared interruptions.

This does not authenticate origins/references, prove that a thumb was valid or an interruption physically succeeded, validate the full corpus, measure acoustic/timing distributions, or establish resource/queue/index/journal bounds and stable memory after warmup. Those required gates need their own runnable evaluation and reviewed evidence. Do not relabel a synthetic trace as a real product run.


## Resource observation evaluator

The capture endpoint now also supplies numeric used/limit pairs for rolling visual bytes/frames, pin bytes/count, model image workers, pending integration execution, confirmations plus cancellations, document-index and staging bytes, and operation-journal bytes. It includes total indexed-file and unresolved-operation counts without document paths, names, payloads or credentials. These are sampled observations, not continuous high-water marks or every application queue.

```powershell
uv run python -m reachy_brain.evals.workload_resources --input local-data/evidence/workload-observations.jsonl --output local-data/evidence/workload-resources.json
```

Use a new output file. Input is capped at 32 MiB, 7,204 records and 128 KiB per line. The report binds the exact input SHA256. The default warmup is 600 seconds; declare any alternative with `--warmup` before collecting the run, rather than choosing it afterward to hide growth. The evaluator requires a complete 90-minute observation, ordered continuous samples, a stable application owner, fresh resource measurements and all listed bounds. Observed over-limit values, changed limits and monotonic post-warmup RSS growth fail. Missing duration, samples or measurements block. Backend and child RSS are reported separately with counts, first/last/peak/median and net change.

A report without those failures is `review_required`, never an acceptance pass. Exit zero means the observed subset is ready for further review; fail/blocked/invalid inputs return non-success. Oscillating upward growth, process attribution, separately launched browser memory, activity authenticity, between-sample peaks, uninstrumented queues and all required timing/physical gates still need evidence. No criterion is satisfied merely by an Idle run or absent observed violations. `acceptance_pass` and `physical_qualification` remain false.

A two-second actual application Idle capture at `local-data/evidence/stage-resource-app-observation/` produced four observations and an appropriately blocked report for missing 90-minute duration and post-warmup samples. This demonstrates the recorder/evaluator path, not a completed soak or physical validation.


## Backend timing distributions

Workload observations include the latest 32 document completions, 64 tool-execution completions and 32 response records, with sequence totals and a distinct response owner. Document samples are local search/read/refresh durations. Tool samples contain only read/draft/write/unknown categories, coarse outcomes and monotonic elapsed time; no account, tool key, payload or operation ID is recorded. Response samples retain existing content-free backend stages and recognition timing. These bounded windows make sample loss detectable when the recorder cannot keep up.

```powershell
uv run python -m reachy_brain.evals.workload_timings --input local-data/evidence/workload-observations.jsonl --output local-data/evidence/workload-timings.json
```

The command caps input at 32 MiB, 7,204 records and 128 KiB per line, requires start/completion markers, hashes the input and refuses to overwrite output. It deduplicates sequence/owner pairs across snapshots, permits running response records to acquire stages, rejects changed completed records/counters/categories/nonfinite durations, and reports missing sequence counts and unfinished responses. Start recording before the measured work; preexisting samples that have fallen out of the buffers will be reported as missing rather than silently excluded.

Reports separate operation/outcome/stage distributions with count, median, nearest-rank p95 and slowest in seconds. Missing sequences or unfinished responses produce `blocked`; otherwise the report is only `partial_report`. Exit zero is successful production of this partial report, not acceptance. `acceptance_pass` and `physical_qualification` remain false. Document samples exclude external worker admission wait. Tool execution includes validation/queue/journal waits but excludes proposing, waiting for user confirmation and separate reconciliation calls. Backend first-audio dispatch does not measure audible response or interruption cutoff. Source capture/archive, gesture recognition/commit, combined retrieval deadline and physical audio measurements still need their separate required evidence.


Capture/perception timing update: workload snapshots also retain 64 samples each for `capture` and `perception`. The existing timing report includes these families. Capture categories distinguish camera/screen/upload/unknown archive requests, with successful, failed and canceled HTTP handling durations. Measurement starts after authentication and ends after upload/decode/archive processing; it excludes browser capture/encoding, pre-handler network transfer and camera exposure. Robot archive capture does not use this HTTP measurement path and still needs separate end-to-end timing.

Perception samples are the worker-reported processing seconds on returned frames. They exclude queue waits, dropped frames, temporal wave/thumb accumulation and accepted-turn commit; processing may complete for a source later rejected as stale. Invalid timing values create a visible missing sequence rather than a fabricated zero-duration sample. These distributions do not qualify gesture recognition/commit latency or physical performance.


Combined retrieval timing update: the `retrieval` stream now records one `combined_retrieval` sample per answer that uses selected visual evidence or visual/document tools. It starts at the existing shared retrieval-budget boundary and ends at the last retrieval result/limit decision, or interruption of pending retrieval. This includes intervening model, workflow and approval time charged to that budget; later answer generation is excluded from elapsed time. Outcomes are coarse answer-path success/error/cancellation categories, with the configured deadline retained per sample. No retrieval use creates no sample. The stream is bounded to 64 entries and scoped to the conversation owner.

The timing command now reports these distributions and the configured retrieval limits, and marks recorded successful completions exceeding their deadline as `fail`. An exhausted/denied request can be reported after the deadline without implying that it executed beyond the deadline; those observations remain in their error/canceled category. Default runtime retrieval limit remains ten seconds, shared with three retrieval rounds. This adds backend combined-budget measurement and supersedes earlier notes that it was missing. Physical capture/gesture/audio and authentic full-workload evidence remain required; the report still cannot grant acceptance.


Gesture timing update: workload snapshots now include a conversation-owned `gesture` stream with up to 64 stage samples. Initially committed thumbs up/down each record three separate intervals: `recognition_observed` (first qualifying candidate's reported capture timestamp to controller recognition), `arbitration` (recognition to poll acceptance), and `commit` (monotonic backend acceptance entry through history/record submission, before transcript delivery). The first interval includes temporal stability and reported-clock processing delay; it is not an independent physical exposure-to-recognition measurement. The maximum reported clock uncertainty across candidate frames accompanies the samples.

The timing report groups these stages/classes separately and preserves maximum reported source uncertainty. Missing/invalid uncertainty rejects the artifact; uncertainty above the controller's 100-ms eligibility bound blocks the report. Rejected/duplicate gesture commits produce no timing sample. Later speech can supersede an initial commit, so these counts do not prove 20 valid final thumb responses. Physical recording/review and final accepted-turn evidence remain required. This supersedes the earlier absence of backend gesture commit measurements, not the outstanding physical gesture qualification.


Calendar completion timing: for calendar_write interval records, at is the start and end is the observed completion; cadence uses end. For point records with no end, at must be the observed completion. Do not use dispatch time as completion. The checker sorts cycles by completion, so their required transport alternation is also evaluated in completion order. Retained operation evidence must substantiate these times; declarations alone remain unqualified.

Calendar identity requirement: each calendar_read/calendar_draft/calendar_write activity must carry nonblank module and account fields (each at most 128 characters), identical within a cycle. Missing, blank or mixed namespaces invalidate cycle coverage. Obtain these values from the actual configured tools and retained operation evidence; do not invent identities to repair old declarations. Backend timing samples deliberately omit tool/account identity, so timing counts alone cannot establish these cycles. This schema extension does not authenticate records or qualify actual workload execution.

Calendar sequence timing: each read must complete before or at the draft start, and each draft before or at the write start. For interval records use end as completion; a point record uses at as its observed completion. Overlapping sequential steps invalidate that cycle. Exact boundary handoffs are allowed. Retain actual timestamps and outcomes in supporting evidence; the checker does not authenticate declared completion.

Active-window consistency: overlapping windows must agree on camera, screen, waves, thumbs and supported-document count. Conflicting overlaps reject the trace rather than allowing a broad active declaration to conceal an outage. Identical overlapping observations are deduplicated for duration, and state changes at a shared endpoint are valid. Record actual changes as separate intervals; consistency does not prove physical continuity.

## Bind the component reports

Run `uv run python -m reachy_brain.evals.workload_bundle --manifest local-data/evidence/soak-run/bundle.json --output local-data/evidence/soak-run/combined.json`. The manifest contains `capture` and `activities`, each with a relative `file` and exact SHA-256 `sha256`. Files must stay inside the manifest directory. Capture limits remain 32 MiB/7,204 lines/128 KiB per line; activity input is at most 4 MiB, manifest 64 KiB. Output must not exist.

The declared duration must match the capture header. The report evaluates coverage, resources with the fixed 600-second warmup, and timings from the same capture. Missing project/tools/response/capture/perception/retrieval/gesture timing families block this combined report. Family presence alone does not prove every required scenario. Component failures remain failures, incomplete observations remain blocked, and the best outcome is review_required. Exit zero means that review-required report was produced; acceptance_pass and physical_qualification remain false. Retain the bound artifacts and manifest.

This does not authenticate timestamps, operation/account identity, live-source continuity, browser memory or acoustic evidence. The actual workload, independent review and a reviewed physical soak gate remain required; this combined report is supporting evidence only.

## Bind independent review records

`workload_review.ReviewedWorkloadBundle` extends the component bundle with plan, recording, browser_resources, operation_evidence, task_evidence and review artifacts, each an in-root file and exact SHA-256. The review uses WorkloadReview: profile pc, reviewer identifier, artifacts mapping every other artifact name to its SHA-256, frozen_at/recorded_at/reviewed_at timestamps and reviewed_activity_ids matching the declaration exactly. Freeze precedes recording; review follows the full declared run duration.

The required true review fields are timeline_alignment_reviewed, complete_recording_reviewed, workload_and_source_continuity_reviewed, activity_outcomes_and_authorization_reviewed, browser_and_process_memory_reviewed, stable_memory_after_warmup_reviewed and timing_scenario_coverage_reviewed. Only an actual independent review can substantiate these statements. Supporting artifacts are bounded to 32 MiB each, recording 512 MiB, review 2 MiB; large data is stream-hashed. No recordings or human judgments are generated.

`score_reviewed_bundle(root, bundle)` retains component failures and binds declared review, but never marks physical qualification or acceptance. Browser-memory and other supporting files require independent interpretation; hashes alone cannot grade them. The live-PC artifact entrypoint below is available; complete independent physical qualification remains outstanding.


## Run the reviewed PC artifact check

Set private `IAGO_PC_WORKLOAD_MANIFEST` to a JSON manifest containing `fixture_kind: physical-pc-independent-recording`, `profile: pc`, nonempty operator/reviewer/recorded_at/application_revision/devices metadata, and `bundle` matching ReviewedWorkloadBundle above. The manifest is bounded to 64 KiB. Artifact paths resolve inside its directory. The activity declaration must report origin `reported-real`; that declaration alone does not prove recording origin. Supply only actual independently reviewed evidence here.

```powershell
uv run --extra vision --extra robot --extra robot-camera iago-verify --suite live-pc --select 'test_physical_workload' --output local-data/evidence/reviewed-pc-workload
```

`PC-WORKLOAD-REVIEWED-BUNDLE` checks artifact binding, review coverage and existing component outcomes. Missing manifest or incomplete component evidence is blocked; failed components, incorrect profile, synthetic origin and invalid artifacts fail. Measurements retain the manifest hash and scored component evidence before outcome assertions. A passing artifact check still reports `review_required`, `acceptance_pass: false` and `physical_qualification: false`: it cannot authenticate a human declaration or interpret the supporting recordings. Independent qualification and all separate acoustic and product acceptance gates remain required. No source capture, workload execution, provider call or recording review is performed by this entrypoint.

Synthetic entrypoint regression: `stage-workload-entrypoint-reviewed/offline-subset.json` has 25 passing checks across bundle/review/entrypoint tests. The actual live-PC subset `stage-workload-pc-prerequisites/live-pc-subset.json` is blocked for the missing manifest. Neither supplies a physical soak pass.
