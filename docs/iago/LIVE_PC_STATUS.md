# Live-PC evidence status

Latest complete PC report: `local-data/evidence/stage-current-pc-native/live-pc.json` contains 11 selected checks: one actual native media pass, ten blocked, zero failures/unexecuted. Command: `uv run --extra vision --extra robot --extra robot-camera iago-verify --suite live-pc --output local-data/evidence/stage-current-pc-native` with process-scoped `IAGO_PC_NATIVE_MEDIA_CHECK=1`. C922 camera/Yeti X microphone acquisition, two archive frames and camera clearing passed on actual Chromium 151.0.7922.34. No acoustic, optical, speech or perception qualification. The blocked records cover ordinary/integration cutoff, desk observation, echo, opening words, controlled entries, response latency, live thumbs, screen grounding and whiteboard/phone grounding. See the report for exact environment prerequisites. Supersedes earlier six-check audits; PC devices are available.

New controlled-entry subset: stage-pc-entry-prerequisites/live-pc-subset.json has one blocked check requiring IAGO_PC_ENTRY_MANIFEST. See WAVE_EVAL.md; this is not a new full live-PC audit.

Latest live thumb subset: stage-pc-live-thumb-prerequisites/live-pc-subset.json contains one blocked PC-LIVE-THUMB-REVIEWED-DATASET check. It requires IAGO_PC_LIVE_THUMB_MANIFEST; see THUMB_EVAL.md. This is additional coverage, not a new complete live-PC audit.

Earlier six-check audit command:

```powershell
uv run --extra vision --extra robot iago-verify --suite live-pc --output local-data/evidence/stage-live-pc-prerequisite-audit
```

All six currently collected checks are blocked by missing configured evidence. No check passed or failed; no selected check was left unexecuted. The requested suite correctly returned non-success. This is a prerequisite audit, not a physical session.

| Check | Required private configuration | Preparation and rerun details |
| --- | --- | --- |
| Physical speaker cutoff | `IAGO_PC_ACOUSTIC_MANIFEST` | [Acoustic evaluation](ACOUSTIC_EVAL.md) |
| Cutoff during integration activity | `IAGO_PC_INTEGRATION_ACOUSTIC_MANIFEST` | [Acoustic evaluation](ACOUSTIC_EVAL.md) |
| Ordinary response latency | `IAGO_PC_RESPONSE_MANIFEST` | [Response evaluation](RESPONSE_EVAL.md) |
| Interruption opening words | `IAGO_PC_OPENING_FIXTURES` | [Opening-word evaluation](OPENING_WORDS_EVAL.md) |
| Reviewed screen bundle | `IAGO_PC_SCREEN_FIXTURES` | [Screen evaluation](SCREEN_EVAL.md) |
| Reviewed whiteboard/phone bundle | `IAGO_PC_VISUAL_FIXTURES` | [Visual evaluation](VISUAL_EVAL.md) |

Supply the actual independent recordings/reviewed records described by the applicable guide, configure the private path and rerun the command or its documented selector. Do not fill records with synthetic data to obtain a physical pass. Screen/visual bundle success establishes the explicitly reported record checks, not automatic physical-origin attestation or full vision qualification.

These six tests do not establish complete coverage of ACCEPTANCE.md. Remaining work includes complete ten-minute echo evidence, live entry/wave/thumb controller datasets and desk observation, actual device lifecycle/permission scenarios, full physical K1 queries including spoken turns, integration settings/workflow demonstration, the thirty-minute product run and ninety-minute mixed-workload soak. Capture origin, freeze chronology and independent review remain required. Robot qualification is a separate tier. Review source paths and existing evidence when closing each gap; do not invent reduced workloads or thresholds.

FEATURES.json retains all 36 feature IDs and separate implementation/validation states. Missing fixtures are not the only remaining work, so this audit does not mark the active build goal blocked or complete.

A seventh live-PC check, `PHYSICAL-PC-REVIEWED-ECHO`, now requires `IAGO_PC_ECHO_FIXTURES`; see [speech observation](SPEECH_OBSERVATION.md). Its separate `stage-echo-physical-prerequisite/live-pc-subset.json` run is blocked by the missing bundle. The earlier six-check result above is historical and does not include this addition. A reviewed-record scorer now exists for ten-minute echo observations; actual recording collection, microphone-continuity evidence and independent review remain outstanding.

An eighth live-PC check now covers actual native browser media acquisition, enabled by `IAGO_PC_NATIVE_MEDIA_CHECK=1`. `stage-pc-native-full-chromium/live-pc-subset.json` passed with actual C922 camera/Yeti X microphone access, camera archive ingress and stop/clear cleanup. It uses full Chromium in headless mode with no fake media. This demonstrates available PC acquisition hardware; it does not qualify physical speech, optical accuracy, perception or the pending reviewed datasets. The earlier six-check audit and separate echo prerequisite result remain historical scopes. See [deployment](DEPLOYMENT.md) for invocation.

The natural desk subset stage-pc-desk-prerequisites/live-pc-subset.json collects one blocked check requiring IAGO_PC_DESK_MANIFEST; see WAVE_EVAL.md. This is not a new complete live-PC audit.


Additional workload artifact gate: PC-WORKLOAD-REVIEWED-BUNDLE is now collected in live-PC. Its standalone prerequisite report, `local-data/evidence/stage-workload-pc-prerequisites/live-pc-subset.json`, selected one check and blocked for missing IAGO_PC_WORKLOAD_MANIFEST. The prior complete 11-check suite predates this addition; this subset does not replace that full report. See WORKLOAD_CAPTURE.md for manifest and exact invocation. Artifact-check success alone cannot grant physical qualification.


Additional K1 record gate: PC-K1-REVIEWED-QUERY-BUNDLE binds 55 UI query records, at least ten spoken queries and independent answer/UI review declarations. The subset stage-k1-pc-prerequisites/live-pc-subset.json selected one blocked check requiring IAGO_PC_K1_MANIFEST; see K1_REVIEW.md. The earlier complete 11-check report predates both workload and K1 additions. No real query session was captured and no physical qualification is inferred.
