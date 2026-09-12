# Recorded thumbs response evaluation

Captured-stream assembly: `uv run python -m reachy_brain.evals.gesture_capture_export --capture local-data/live-thumb-observations.jsonl --output-directory local-data/live-thumb-exports`. The destination must not exist. This validates the entire bounded stream, completion count, adjacent continuity and retained coverage across its full interval before writing start.json, end.json and capture-binding.json. Use those exports as the bundle's start/end artifacts. The binding report retains the exact source capture digest. No labels or human review are generated. The current two-endpoint scorer requires all interval events still present in the final 512-row export: the assembler rejects overflow even if earlier polls captured the missing rows. Plan the bounded dataset run accordingly; do not silently drop cases or count a partial interval as a full dataset. Physical recording, frozen labels, source/question/feedback review and original acceptance thresholds remain mandatory.

Continuous observation capture: `uv run python -m reachy_brain.evals.gesture_capture --duration 1800 --interval 2 --output local-data/live-thumb-observations.jsonl`. Start the application/control session first and configure IAGO_LOCAL_CONTROL_TOKEN privately. Default origin is loopback port 8765. No camera/provider operation is started by this collector. It validates adjacent observation continuity and flushes JSONL with complete_capture or incomplete status. Limits: two hours, 1–30-second polling, 512 KiB responses, 32 MiB output; output must not exist. Missing events or replaced/unavailable owners abort. Independent camera/feedback recordings, alignment, stream assembly and real question/source labeling remain required. Capture success is not physical qualification.

Run `uv run --extra vision --extra robot iago-verify --suite offline --select recorded_thumbs --output local-data/evidence/recorded-thumbs` with private `IAGO_THUMB_CLIP_MANIFEST` set to a frozen real-camera manifest. Missing recordings or vision prerequisites are blocked. This required gate is part of the full offline suite. Re-run the existing wave dataset unchanged using [WAVE_EVAL.md](WAVE_EVAL.md); thumbs coverage does not replace waves.

Prepare at least 20 real thumbs-up, 20 real thumbs-down and 20 negative recordings. Include neutral/open hands, waves, pointing, drawing, phone use, camera motion and ambiguous/multiple people among negatives. For each positive class use varied orientation, distance and lighting; the manifest requires at least two distinct labels for each condition, which the reviewer must verify against the actual recordings. Maintain separate tuning media, retain original private recordings and freeze all labels/hashes before scoring. Software cannot authenticate human review or recording provenance from JSON declarations.

Use the frame structure and bounds in WAVE_EVAL.md: 2–10 seconds, zero-origin sample times with 50–200 ms gaps, 11–151 in-root hash-verified frames per clip, at most 1 MiB encoded per image and the production image-dimension bound. Manifests are at most 5 MiB and contain 60–200 clips. Exact duplicated sequences or IDs cannot inflate counts. The example below is abbreviated and must be completed with real labels and every frame:

```json
{
  "fixture_kind": "real-prerecorded-camera",
  "operator": "reviewer identifier",
  "frozen_at": "ISO date/time",
  "human_reviewed_labels": true,
  "clips": [{
    "id": "unique-recording-id",
    "label": "thumb_up",
    "category": "thumb_up",
    "camera": "recording device/source",
    "people": 1,
    "orientation": "front facing",
    "distance": "one meter",
    "lighting": "daylight",
    "question_presented_ms": 0,
    "frames": [{
      "file": "recording-id/frame-000.jpg",
      "sha256": "64 lowercase hex characters from exact image bytes",
      "at_ms": 0
    }]
  }]
}
```

Labels are `thumb_up`, `thumb_down`, `negative`. Positive categories match their labels. Negative categories are `neutral`, `open_hand`, `wave`, `pointing`, `drawing`, `phone_use`, `camera_motion`, `ambiguous_people`. The expected positive outcomes are exactly one yes/no response respectively; negative examples expect no response. The proposed small-set false-response allowance remains at most one across the entire negative set, while invalid question/person context, duplicate responses and wrong polarity are hard failures. A positive recording requires one labeled person and an active question. For no-question negatives use `question_presented_ms: null`.

The evaluator presents one controlled question at the labeled time, then feeds real detector observations into the production ThumbController and polls it only inside the recorded interval. It counts accepted responses with exact question ID and turn binding, not raw hand labels. Include sufficient neutral release after question presentation and movement settling, followed by a stable gesture and enough trailing frames for arbitration. No synthetic extra poll after the recording can turn a pending classification into a pass. Do not label a gesture held before the question as an eligible positive response.

Reports retain accepted values/question/turn/times, detected person-count distribution, controller feedback counts, missed IDs, separate up/down recall, false responses and hard-failure counts. Labeled person counts do not override detector output or force association. Pass requires at least 90% accepted recall for each class (18/20 at minimum), at most one negative false response, and zero wrong-polarity, duplicate or invalid-context responses. Larger datasets retain percentage recall gates and the same false-response maximum.

Replay shares the production detector worker and PerceptionEvents path with wave evaluation, using controlled timestamps and fresh state per clip. The camera source identity is evaluation instrumentation; recorded/historical input remains ineligible for gestures in the production application. This check establishes only prerecorded controller behavior under its declared timeline. It does not establish live webcam UI feedback, audio/speech races, actual Astra reception, physical room accuracy or Reachy qualification. Keep all existing deterministic P10 policy, live-provider and separate live-webcam/robot datasets mandatory. No recordings are captured automatically and no personal media belongs in Git.

Live evaluation preparation: authenticated `GET /api/gesture-activity` exposes initial controller acceptances and speech supersessions independently of saved transcripts. It uses an owner ID, monotonic elapsed times, sequence numbers, bounded question/source/slot identifiers, yes/no values and source generations. Only 512 recent rows are retained; dropped_samples and incomplete flags must prevent a complete-observation claim. Capture repeatedly and preserve sequence continuity for a future evaluated run. The endpoint retains no question text/images and uses Cache-Control: no-store. It does not count missed gestures, prove source origin, measure UI delivery or replace independent camera recordings and frozen labels. A complete live-PC/robot scorer and capture workflow remain required.

Controller observation continuity can now be checked with:

```powershell
uv run python -m reachy_brain.evals.gesture_observation --start local-data/gesture-start.json --end local-data/gesture-end.json --output local-data/gesture-observation.json
```

Use exact authenticated endpoint exports from the same owner, taken before and after the observation interval. Inputs are limited to 512 KiB each; output must not exist. The checker rejects missing interval samples, changed overlapping events, counters/times inconsistent with the sequence, replaced owners and incomplete metadata. Pre-baseline drops are allowed only when every event in the evaluated interval remains available. For longer runs capture multiple overlapping intervals before the 512-row limit loses interval events. The result binds snapshot hashes and preserves acceptance and supersession counts separately. `event_coverage_complete` means backend event coverage only; `physical_gesture_validated` and `release_validated` remain false. Live labels, question/source binding, UI feedback and independent media review/scoring still need their complete evaluation workflow.

`reachy_brain.evals.live_thumbs` now provides `LiveThumbPlan` and `score_live_thumbs(plan, start, end)` for numeric live-controller interval assessment. Plans have a declared pc/reachy_pc/reachy_local profile, observation owner and 60–200 cases. Each case supplies id, label/category, half-open start/end on the owner elapsed clock, source/generation, session/question/turn, person count and orientation/distance/lighting. Use null question for no-question negatives. Positive cases require one person and a question; varied positive conditions and all negative categories are mandatory. Intervals cannot overlap.

Every observed acceptance must belong to a case; exact context mismatches, duplicates and wrong polarity fail regardless of recall. Reports preserve the original 90% per-class and at-most-one-negative-false-response thresholds and list missed cases. Initial acceptances remain counted even if speech later supersedes them. Numeric target success cannot prove physical origin, camera accuracy, question eligibility or UI feedback. Bound frozen labels, source/timeline review, independent media and live-PC/robot runner/capture workflow remain required before this supports a physical acceptance claim.

Live artifact binding is available through `live_thumb_bundle.LiveThumbBundle` and `score_bundle(root, bundle, profile=...)`. A bundle references plan/start/end/recording/review by relative file and SHA-256. Root escape is rejected. Plan/review JSON is limited to 2 MiB; observation exports to 512 KiB; recordings are stream-hashed up to 512 MiB. Preserve originals privately.

The strict review records matching profile and all four input hashes, frozen_labels_sha256 from `label_digest(plan)`, numeric frozen_at/recorded_at/reviewed_at chronology, reviewer, source_kind independent_live_camera_recording and every unique reviewed_case_id. Require true live_source_reviewed, question_eligibility_reviewed, timeline_alignment_reviewed, feedback_reviewed and complete_observation_reviewed. Freeze label IDs/classes/categories/person counts/conditions before capture; runtime source/question IDs and elapsed intervals are assigned during the captured run and separately reviewed. Retain independent proof of freeze chronology and source/feedback/timing review. Hashes and declared timestamps alone cannot authenticate these facts. A bound valid bundle remains numeric evidence with physical/release validation false. The live-PC/robot entrypoints and capture workflow remain unfinished.

Live reviewed-record entrypoints are now runnable, superseding the earlier pending-entrypoint notes:

```powershell
uv run --extra vision --extra robot --extra robot-camera iago-verify --suite live-pc --select test_pc_live_thumb_dataset --output local-data/evidence/pc-live-thumbs-reviewed
uv run --extra vision --extra robot --extra robot-camera iago-verify --suite robot --select test_robot_live_thumb_dataset --output local-data/evidence/robot-live-thumbs-reviewed
```

Set private IAGO_PC_LIVE_THUMB_MANIFEST, IAGO_REACHY_PC_LIVE_THUMB_MANIFEST and IAGO_REACHY_LOCAL_LIVE_THUMB_MANIFEST separately. Each manifest (maximum 1 MiB) contains bundle (the five artifact references above), fixture_kind physical-pc-independent-recording or physical-robot-independent-recording, operator, reviewer, recorded_at, application_revision and devices. Robot manifests additionally require matching profile, robot_identity, daemon_version and configuration_sha256. Missing manifest paths block; invalid supplied evidence fails. Numeric failures retain the full score and manifest digest. No profile can qualify another, and reviewed-record processing remains conditional on independent physical origin/chronology verification. A reproducible live capture workflow and real recordings remain required.
