# Robot-local media qualification

The robot suite includes a real local SDK media-acquisition check. It covers one portion of Reachy qualification. All Wi-Fi, acoustic, motion, capability, clock, perception and soak gates in SPEC.md and ACCEPTANCE.md remain required. Offline fake-device tests do not qualify hardware.

Run on the physical robot Linux runtime beside the existing Reachy daemon, with locked robot dependencies and working local GStreamer Python bindings. Stop other Iago edge/standalone media owners first, keeping the daemon running. Follow DEPLOYMENT.md for setup. This check does not spawn a daemon or use a simulator. Explicit enablement declares physical hardware and permits brief camera/microphone acquisition and the adapter's flush/close cleanup. Cleanup cancels existing movement; the probe submits no new head/antenna target and plays no sound.

```sh
IAGO_ROBOT_LOCAL_MEDIA_CHECK=1 uv run --extra robot iago-verify --suite robot --select robot_hardware_media --output local-data/evidence/robot-local-media
```

Without that setting, local Linux, SDK or GStreamer binding, the check is blocked. Once enabled with prerequisites available, connection/media failures fail. A separate child uses ReachyLocalMedia with ordinary localhost-only, local-media, no-daemon-spawn configuration. A 30-second parent deadline bounds initialization, acquisition and cleanup. Timeout terminates the child and fails; inspect/restart the media owner as needed afterward. Forced process termination does not prove graceful hardware cleanup.

For five seconds, poll actual camera and microphone data. Validate bounded BGR uint8 frames, count frames and distinct hashes, count microphone chunks/scalars and peak amplitude, and validate a source JPEG. Flush the player and require microphone capture within two seconds. Close the normal adapter in finally. Report rates/channels from the adapter. Require at least two valid frames, two audio chunks, a valid JPEG and post-flush audio. Silent samples can establish acquisition but not intelligibility.

Only numeric summaries and dimensions are exported. Images, audio and individual image hashes are not persisted. The child writes an exclusive temporary result file. SDK console output is suppressed; failed child status requires inspecting local daemon/media diagnostics. Retain actual robot identity, firmware/daemon versions and deployment configuration alongside verifier platform/dependency/commit evidence for a complete record.

Repeated frames may be duplicates: hash diversity does not prove freshness, and poll count is not effective detector FPS. JPEG validity does not prove readability. A flush call does not measure speaker silence. Both voices, opening-word preservation, physical cutoff, watchdog timing, PC loss, motion interference, standalone capability, PC-source restrictions and resource stability remain separate required checks.


## Independent recorded speaker cutoff

The robot suite now separately scores assisted (`reachy_pc`) and standalone (`reachy_local`) acoustic recordings. Scoring can run on the PC; acquisition must have used the real robot speaker and declared profile. Configure IAGO_ROBOT_REACHY_PC_ACOUSTIC_MANIFEST and IAGO_ROBOT_REACHY_LOCAL_ACOUSTIC_MANIFEST privately with the respective manifest paths, then run:

```powershell
uv run --extra vision --extra robot --extra robot-camera iago-verify --suite robot --select test_robot_physical_acoustics --output local-data/evidence/robot-cutoff
```

Each bounded 1-MiB JSON manifest declares fixture_kind `physical-robot-independent-recording`, its exact profile, operator, recorded_at, application_revision, devices, voice_ids, robot_identity, daemon_version, configuration_sha256 and reviewer, plus trials using CutoffTrial from reachy_brain/evals/acoustics.py. Use ACOUSTIC_EVAL.md for independent PCM capture, calibrated event alignment, frozen thresholds, SHA256, channel isolation and trial fields. Record the physical robot identity, firmware/daemon versions and deployment setup accurately; a string declaration cannot authenticate physical origin by itself. Independent review must substantiate those declarations.

Supply at least 20 spoken interruptions per profile, unique recorded events, and both Stop/spoken groups for OpenAI, chosen ElevenLabs and fallback. Every group retains p95 strictly below 150 ms Stop / 300 ms spoken. Reports retain manifest hash, actual sample counts, declared hardware metadata and separate group distributions. Partial measured results survive a later recording error. Missing manifests block; malformed or incorrectly scoped supplied fixtures fail. PC fixtures cannot qualify a robot profile, and assisted results cannot qualify standalone. No physical recording was produced by adding this check.

This adds the ordinary recorded cutoff check only. Integration-load cutoff, opening words, echo, Wi-Fi/PC loss and measured watchdog, motion interference, optical/perception datasets and onboard soak still require separate evidence and their full required harnesses. No whole robot tier is passed by this scorer.


## Cutoff while integrations are blocked

Configure IAGO_ROBOT_REACHY_PC_INTEGRATION_ACOUSTIC_MANIFEST and IAGO_ROBOT_REACHY_LOCAL_INTEGRATION_ACOUSTIC_MANIFEST for separate profile recordings. Run:

```powershell
uv run --extra vision --extra robot --extra robot-camera iago-verify --suite robot --select test_robot_integration_acoustics --output local-data/evidence/robot-integration-cutoff
```

Use the same bounded metadata contract as the ordinary cutoff manifest above, but fixture_kind must be `physical-robot-independent-integration-recording`, and each trial must satisfy IntegrationCutoffTrial in reachy_brain/evals/integration_acoustics.py. In addition to independent audio and isolation review, supply a bounded retained activity trace with SHA256, reviewed alignment and the actual blocked interval on the recording sample clock. The activity must span interruption uncertainty, measured cutoff and the subsequent half-second silence observation. A trace hash binds bytes; independent review must establish that the real activity occurred on the declared runtime.

Each profile requires MCP read, MCP write, credential refresh, skill loading and reconciliation coverage, each with OpenAI, chosen ElevenLabs and fallback, for both Stop and spoken interruption, plus at least 20 spoken trials total. All groups retain the original strict p95 cutoff thresholds. Repeated recording events cannot count as different activities or voices. Existing PC integration trials cannot qualify robot output. Missing fixtures block; malformed metadata, absent coverage, unreviewed/misaligned activity or failed cutoff targets fail.

This closes the missing recorded E1 robot audio-priority check, not physical validation. It does not establish robot-local motion supervision, watchdog/PC-loss stop, echo/opening words or onboard workload bounds. Collect and assess those separately under the original contract.


Recorded-event identity: ordinary cutoff trials, like integration cutoff trials, reject repeated (recording SHA256, event sample) pairs even when channels differ. Multiple channels observing one interruption are not independent trials and cannot fill provider groups or minimum sample counts. Retain additional channels as supporting evidence for the same event.


## Opening words during spoken interruption

The shared opening-word scorer now has separate robot-profile entrypoints. Set IAGO_ROBOT_REACHY_PC_OPENING_FIXTURES or IAGO_ROBOT_REACHY_LOCAL_OPENING_FIXTURES to the corresponding private bundle directory. Run:

```powershell
uv run --extra vision --extra robot --extra robot-camera iago-verify --suite robot --select test_robot_opening_words --output local-data/evidence/robot-openings
```

Follow OPENING_WORDS_EVAL.md for plan.json, mapping.json, review.json, acoustics.json, session.json and retained independent WAV files. Preserve at least 20 frozen interruption openings, all three voice paths, unique recognition entries/events, recorded overlap and human-reviewed mappings. Acoustics metadata must identify `physical-robot-independent-recording`, the exact reachy_pc/reachy_local profile, robot_identity, daemon_version and configuration_sha256, plus the existing operator/time/revision/devices/voice fields. A PC or other-profile recording cannot qualify this run. No additional provider call is made during scoring. Missing bundle files block; incorrect provenance, binding, review or missing opening words fail.

Passing this check establishes only the reviewed opening-word requirement in its declared profile. Ordinary and integration cutoff thresholds, echo, PC-loss/watchdog, motion and soak remain separate gates. Recording declarations require independent substantiation; these tests generate no physical recordings.


## Ordinary audible response latency

Set IAGO_ROBOT_REACHY_PC_RESPONSE_MANIFEST and IAGO_ROBOT_REACHY_LOCAL_RESPONSE_MANIFEST to separate reviewed recording manifests, then run:

```powershell
uv run --extra vision --extra robot --extra robot-camera iago-verify --suite robot --select test_robot_response_latency --output local-data/evidence/robot-response
```

Follow RESPONSE_EVAL.md and ResponseTrial for independent speaker-channel recording, actual speech-end alignment, calibrated threshold, isolation/turn review, voice identity and ordinary/retrieval classification. Robot manifests require fixture_kind `physical-robot-independent-recording`, exact profile, robot_identity, daemon_version and configuration_sha256 plus existing operator/time/revision/device/voice metadata. Both robot profiles reject PC or other-profile recordings before measurement. Preserve manifest/WAV hashes and independent substantiation of physical origin.

The shared scorer reports per-voice ordinary response count/median/p95/slowest, requiring ordinary samples for OpenAI, chosen ElevenLabs and fallback with median strictly below 3 seconds and p95 below 5 seconds. Retrieval is reported separately and cannot substitute for ordinary samples. Backend first-audio dispatch is not audible response onset. This does not qualify cutoff, echo, optical grounding or onboard soak. Missing manifests block; invalid supplied evidence or failed targets fail.


## Reviewed echo observation

Set IAGO_ROBOT_REACHY_PC_ECHO_FIXTURES and IAGO_ROBOT_REACHY_LOCAL_ECHO_FIXTURES to independent profile-specific bundle directories, then run:

```powershell
uv run --extra vision --extra robot --extra robot-camera iago-verify --suite robot --select test_robot_echo --output local-data/evidence/robot-echo
```

Use the capture/review/start/end/WAV procedure in SPEECH_OBSERVATION.md. capture.json must validate as RobotEchoCapture: physical-robot-independent-recording, exact reachy_pc/reachy_local profile, nonempty robot_identity and daemon_version, and a 64-hex configuration_sha256, in addition to all existing capture fields. Review digest covers the entire capture including robot/profile fields; a PC review or a changed profile requires its own independently substantiated review. Retain actual robot speaker and microphone channel isolation, microphone continuity and synchronized backend event coverage.

The existing scorer requires at least 600 seconds of reviewed real assistant playback across both voices/fallback, complete speech-event classification and zero self-triggered turns or unknown events. It preserves all calibration, observation, digest and timing checks. Metadata/hash declarations alone do not authenticate physical origin. Synthetic scorer tests do not qualify robot echo behavior. Missing physical bundles block; invalid evidence or observed self-triggering fails. This echo gate does not replace cutoff, opening-word, loss/watchdog, motion or soak checks.


## Recorded PC-loss audio cutoff

Set IAGO_ROBOT_PC_LOSS_MANIFEST to a private independent recording manifest and run:

```powershell
uv run --extra vision --extra robot --extra robot-camera iago-verify --suite robot --select test_robot_pc_loss --output local-data/evidence/robot-pc-loss
```

Use ordinary robot manifest metadata (physical-robot-independent-recording, reachy_pc, robot/daemon/configuration identity, operator/reviewer/time/revision/devices/voices), with trials validating as LossTrial in reachy_brain/evals/robot_loss.py. Each trial embeds a CutoffTrial recording, trigger pc_pause or pc_terminate, lease_seconds, a bounded trace_file and trace_sha256, human_reviewed_loss and loss_alignment. The nested action remains stop for the shared acoustic primitive; event_sample must denote the independently reviewed PC-loss event, not an application Stop command. Trace review must establish real controller/connection loss, alignment and absence of a separate manual Stop. Hashes bind bytes, not authenticity.

Include both pause and termination with OpenAI, chosen ElevenLabs and fallback, without reusing recorded events across trials. The entrypoint requires lease_seconds to match current PlaybackGuard (1.0 second), so the declared bound cannot be inflated to make a slow stop pass. Record that runtime configuration before collection. Every measured last-audio cutoff including alignment uncertainty must be within that bound; observation must cover the lease plus a half-second silence tail. Later resumed speech counts toward cutoff. Independent isolation/calibration requirements remain unchanged. Missing recordings block and invalid/late evidence fails.

This is an audio-only PC-loss gate. It does not establish head/antenna hold, stale packet rejection after reconnect, actual reconnection behavior, standalone loss behavior or all Wi-Fi faults. Those retain their separate hardware procedures and evidence requirements. Synthetic WAV/trace tests only verify scoring logic.


PC-loss reports include per-provider/per-trigger count, median, nearest-rank p95, slowest cutoff, declared lease values and the number of individual lease overruns. Every trial must still satisfy the lease: a good p95 cannot hide one late stop. Empty or partial groups remain visible through coverage assertions and retained partial measurements; no summary grants complete robot qualification.

## Motion measurement preparation

The independent pose-trace scorer is `reachy_brain.evals.robot_motion.MotionTrace` / `score_motion`. Samples use seconds on one aligned recording timeline and eight pose channels: x/y/z in metres, roll/pitch/yaw and both antenna angles in radians. Provide calibrated per-channel measurement error and event clock uncertainty. Sampling gaps above 0.1 seconds or insufficient before/after coverage reject; record through at least lease plus 0.5 seconds. Report per-channel excursion after the conservative deadline and motion before the event. Displacement is the maximum separation between any two samples in the relevant window: the full range for translation and maximum shortest circular distance for angles. This catches opposite excursions around an intermediate pose while accounting for angular wraparound. The earlier reference-to-first method missed that case and is superseded; the two-error uncertainty threshold is unchanged.

This measures observable pose change only. The 0.1-second sampling requirement is evidence resolution, not a replacement hold-latency target. Noise bounds must come from frozen independent calibration; do not tune them to hide motion. Backend commands or target poses are not physical position samples. Independent sensor origin, calibration and event alignment still require review, and motion between samples remains unobserved. The scorer deliberately never marks physical origin or release validation true. A bound-file physical runner, reviewed calibration/recording bundle and actual hardware trials remain to be completed; it is not yet a new robot-tier pass.

Motion artifact binding is available through `robot_motion_bundle.MotionBundle` and `score_bundle(root, bundle)`. The bundle contains trace/calibration/review/recording references, each with relative file and SHA-256. JSON artifacts are bounded to 2 MiB; the recording is stream-hashed up to 512 MiB. Resolved paths must remain inside the bundle root. Calibration contains the exact ordered channels/error bounds and frozen_at; review binds all three other hashes, recorded_at after freeze, reviewer and true calibration_reviewed/event_alignment_reviewed/pose_extraction_reviewed flags with source_kind independent_pose_recording. Retain independent evidence supporting those declarations; the tool checks binding and declared chronology, not their truth. Current output excludes reviewer text and keeps physical_origin_verified/release_validated false. A robot-tier runner and real measured bundles remain pending.


The robot-tier motion runner is now available (superseding the pending-runner note above). Set `IAGO_ROBOT_MOTION_MANIFEST` to a private manifest and run:

```powershell
uv run --extra vision --extra robot --extra robot-camera iago-verify --suite robot --select test_recorded_pc_loss_motion --output local-data/evidence/robot-motion-reviewed
```

Use fixture_kind physical-robot-independent-recording, profile reachy_pc, robot_identity, daemon_version, configuration_sha256, operator, reviewer, recorded_at, application_revision and devices. Supply 3–1000 trials, each with unique id, trigger (pc_pause, pc_terminate, connection_loss) and MotionBundle. Cover every trigger with distinct trace artifacts. Each bound review must include matching trigger and profile. Trace lease_seconds must equal the current PlaybackGuard lease (one second), not an enlarged test allowance. Each trial must show measurable pre-event motion and no observed post-lease excursion beyond measurement uncertainty. The runner retains manifest hash and completed trial measurements even on failure. Missing manifests are blocked. Passing record processing remains conditional on independent origin/calibration audit and does not qualify audio, recovery, stale replay or between-sample movement.

The recorded-motion entrypoint has offline synthetic bundle coverage in `tests/test_robot_motion_entrypoint.py`: required trigger/profile/lease checks, idle and late-motion rejection, duplicate trial IDs, partial failure observations and missing prerequisites. This verifies gate behavior only. Synthetic declarations are explicitly labeled in the fixture and cannot qualify physical origin; actual independently reviewed recordings remain required.
