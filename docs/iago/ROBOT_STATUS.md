# Current robot qualification coverage

Latest complete robot report: `local-data/evidence/stage-current-robot-prerequisites/robot.json` contains 19 selected checks, all blocked, zero failures/passes/unexecuted. Command: `uv run --extra vision --extra robot --extra robot-camera iago-verify --suite robot --output local-data/evidence/stage-current-robot-prerequisites`. Missing profile-specific reviewed recordings and actual local robot execution remain explicit prerequisites; see retained phase traces and ROBOT_EVAL.md. No robot physical qualification is claimed.

Latest complete robot collection: stage-robot-desk-prerequisites/robot.json contains nineteen checks, all blocked, zero passed/failed. Adds natural desk report gates for both profiles; see WAVE_EVAL.md. Supersedes prior seventeen-check count.

Latest complete robot audit: stage-robot-entry-prerequisites/robot.json collects seventeen checks, all blocked, zero passed/failed. Adds separate assisted/standalone controlled-entry record gates; see WAVE_EVAL.md. Supersedes earlier fifteen-check count.

Latest complete robot invocation: `uv run --extra vision --extra robot --extra robot-camera iago-verify --suite robot --output local-data/evidence/stage-robot-live-thumb-prerequisites`: fifteen collected, all blocked, zero passed/failed/unexecuted. This supersedes the thirteen-check count below and adds separate assisted/standalone live thumb reviewed-record gates. See THUMB_EVAL.md for exact prerequisites.

This supersedes historical statements that the robot suite collects zero checks. It is not implementation completion or physical qualification. SPEC.md Revision 6 and ACCEPTANCE.md remain authoritative for both deployment profiles.

The current complete invocation is:

```powershell
uv run --extra vision --extra robot --extra robot-camera iago-verify --suite robot --output local-data/evidence/stage-robot-motion-current
```

The retained robot.json and robot.md report thirteen selected checks, thirteen blocked, zero passed/failed and zero unexecuted. The invocation completed with non-success as required. Current source base is main at 08fbabe5b00ecd7e574cd7a682f21bf88a556ac7 with existing uncommitted implementation and test changes; consult the report for its actual dirty-tree record.

| Collected check | Current prerequisite | Exact next step |
| --- | --- | --- |
| Local hardware media | Physical robot Linux, running daemon, local SDK/GStreamer and explicit probe enablement | Follow ROBOT_EVAL.md; on the actual robot set IAGO_ROBOT_LOCAL_MEDIA_CHECK=1 and run the robot_hardware_media selector. |
| Assisted ordinary cutoff | Independently reviewed robot-speaker recordings in reachy_pc | Set IAGO_ROBOT_REACHY_PC_ACOUSTIC_MANIFEST and run test_robot_physical_acoustics. |
| Standalone ordinary cutoff | Independently reviewed robot-speaker recordings in reachy_local | Set IAGO_ROBOT_REACHY_LOCAL_ACOUSTIC_MANIFEST and run test_robot_physical_acoustics. |
| Assisted integration cutoff | Independent robot audio and reviewed blocked-activity traces in reachy_pc | Set IAGO_ROBOT_REACHY_PC_INTEGRATION_ACOUSTIC_MANIFEST and run test_robot_integration_acoustics. |
| Standalone integration cutoff | Independent robot audio and reviewed blocked-activity traces in reachy_local | Set IAGO_ROBOT_REACHY_LOCAL_INTEGRATION_ACOUSTIC_MANIFEST and run test_robot_integration_acoustics. |

Use new output directories for new runs. Missing prerequisites block; invalid supplied evidence or failed measurements fail. Synthetic manifests used to exercise rejection are offline tests and never count as physical robot trials. Fixture hashes bind artifact bytes, not their physical origin; independent review remains required.

| Additional collected check | Current prerequisite | Exact next step |
| --- | --- | --- |
| Assisted opening words | Frozen reviewed real robot interruption bundle | Set IAGO_ROBOT_REACHY_PC_OPENING_FIXTURES and run test_robot_opening_words. |
| Standalone opening words | Frozen reviewed real robot interruption bundle | Set IAGO_ROBOT_REACHY_LOCAL_OPENING_FIXTURES and run test_robot_opening_words. |

| Response latency check | Current prerequisite | Exact next step |
| --- | --- | --- |
| Assisted response | Independent physical robot response recordings | Set IAGO_ROBOT_REACHY_PC_RESPONSE_MANIFEST and run test_robot_response_latency. |
| Standalone response | Independent physical robot response recordings | Set IAGO_ROBOT_REACHY_LOCAL_RESPONSE_MANIFEST and run test_robot_response_latency. |

| Echo check | Current prerequisite | Exact next step |
| --- | --- | --- |
| Assisted echo | Independent robot playback/microphone and reviewed event bundle | Set IAGO_ROBOT_REACHY_PC_ECHO_FIXTURES and run test_robot_echo. |
| Standalone echo | Independent robot playback/microphone and reviewed event bundle | Set IAGO_ROBOT_REACHY_LOCAL_ECHO_FIXTURES and run test_robot_echo. |

| PC-loss check | Current prerequisite | Exact next step |
| --- | --- | --- |
| Assisted PC-loss audio | Reviewed physical pause/termination recordings and loss traces | Set IAGO_ROBOT_PC_LOSS_MANIFEST and run test_robot_pc_loss. |

## Requirements beyond the collected checks

The twelve checks above do not constitute complete robot test coverage. These areas still need concrete physical procedures, appropriate capture/scoring integration and retained evidence:

- Broader Wi-Fi failure, locally measured motion hold, stale-output rejection and recovery after PC loss. The newly collected recorded PC-loss audio check does not qualify these remaining parts.
- Local motion bounds, stop/hold supervision and motion-camera interference. The media probe calls cleanup but does not submit or measure head/antenna trajectories.
- Real double-talk assessment, prolonged Conversation/Aware operation and real device loss/recovery. PC acoustic results do not qualify robot speakers/microphones.
- Real camera optical grounding, detail crops and temporal source associations; live presence/wave/thumb datasets with original counts, negatives, accepted turns, clock uncertainty and motion cases. Inference on blank synthetic images cannot qualify these.
- Assisted simultaneous robot camera and PC screen, source loss/clearing, and explicit PC-off source limitations. Test configured PC document sources in assisted mode and separately configured robot-accessible projects in standalone, with the full required retrieval/citation restrictions.
- Actual official WebRTC relay authentication and pinned robot identity, camera freshness and capture/motion timestamp qualification. Local synthetic SDK transport success does not establish relay/device behavior; current uncertain WebRTC gesture provenance remains a material limitation documented in DEPLOYMENT.md.
- Standalone Linux/ARM installation, declared capabilities and measured effective rates/resources; complete robot workload and thermal/queue/storage observations. Windows wheel construction does not prove onboard performance.

Offline owner/lease/protocol/camera/lifecycle tests exercise implementable code and remain valuable evidence. They do not fill missing physical scenarios by changing their tier labels. Conversely, missing robot hardware does not excuse unfinished implementable integration or harness work. Complete that independent work and the final source review before treating hardware/fixtures as the only blockers.

FEATURES.json marks robot tiers with collected blocked checks as blocked and links this report. Other missing coverage remains explicitly unproven. No feature is promoted to implemented or fully validated by this status reconciliation.

Latest software regression: `uv run --extra vision --extra robot --extra robot-camera iago-verify --suite offline --select 'test_robot or test_edge or test_reachy_adapter or test_source_frame_sequence or test_perception_events' --output local-data/evidence/stage-robot-sequence-lifecycle-regression` passed 159, zero failed/blocked/unexecuted. This checks implementation using synthetic media/controlled adapters and existing local transports. It does not change the twelve blocked physical-tier results above or establish complete robot acceptance coverage.

Motion gate: ROBOT-PC-LOSS-RECORDED-MOTION requires IAGO_ROBOT_MOTION_MANIFEST and hash-bound independent calibration/pose recordings for pc_pause, pc_terminate and connection_loss. See ROBOT_EVAL.md. It remains blocked; sampled motion scoring does not establish full physical hold or recovery.
