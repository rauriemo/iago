# Iago execution record

Initial status: handoff materials prepared; application implementation has not started. No tests, live providers or physical devices have been validated here.

## Repository baseline

- Actual project path: `C:\Users\rafa\Projects\iago`.
- Initial branch: `main`; commit: `f51443cac531dc394e6ad0afa7b40878721d0a72`; initial working tree clean. Only tracked file was README.md, preserved unchanged. No existing root or ancestor AGENTS.md was found. Root AGENTS.md now copies the supplied AGENTS.iago.md instructions.
- Windows 11 Pro 64-bit, version 10.0.26200; Python 3.12.10; uv 0.11.15; Git 2.54.0.windows.1; Chrome 152.0.7977.84; Edge 152.0.4191.66. Node 25.7.0 / npm 11.10.1 are available but no JavaScript toolchain is selected or required for preparation. Windows enumerates the c922 Pro Stream Webcam, Yeti X microphone and Pebble V3 speakers as OK; capture, playback and permissions remain untested.
- Checked Process/User/Machine environment variable presence only: OPENAI_API_KEY absent; ELEVENLABS_API_KEY present in Process/User, validity untested; ELEVENLABS_VOICE_ID absent. No DEVELOPMENT_BUDGET or IAGO_DEVELOPMENT_BUDGET found. No local application configuration existed. Credentials elsewhere were not searched. No provider requests or billable calls made.
- Reachy hardware: not yet available according to the current plan.
- Product source: SPEC.md Revision 6 (amended from Revision 5); name for this implementation: Iago.

## Milestones

All milestones belong to the same goal. A blocked physical check does not prevent independent implementation.

| Stage | Scope | Implementation | Validation/evidence |
| --- | --- | --- | --- |
| 0 | Inspect repo; verify upstream contracts/access; establish harness | Not started | Not run |
| 1 | Core state, cancellation, heard history, fakes and queues | Not started | Not run |
| 2 | Real PC full-duplex audio, Astra and OpenAI speech | Not started | Not run |
| 3 | ElevenLabs voice, preview and fallback | Not started | Not run |
| 4 | Camera plus screen sharing, global history/pins, source clearing and uploads | Not started | Not run |
| 5 | Visual/document tools, incremental project index, citations, evidence UI and scenarios | Not started | Not run |
| 6 | Local perception, waves, question-bound thumbs and speech arbitration | Not started | Not run |
| 7 | Aware mode, trigger rules, presets and restraint | Not started | Not run |
| 7a | E1 registry/executor, MCP/direct adapters, credentials, skills, policies, journal/reconciliation, events and fake calendar workflow | Not started | Not run |
| 8 | Windows launch, project/gesture/screen controls, exports, diagnostics, mixed-load demo and soak | Not started | Not run |
| 9 | Real Reachy adapters/edge; camera thumbs and configured screen/document sources; hardware checks | Not started | Not run |
| 10 | Full feature audit, review, regression and handoff | Not started | Not run |

## Current next step

Revision 6 planning amendment is complete; current scope is 36 features. Wait for Rafael to issue docs/iago/GOAL.txt before implementation. Then re-inspect the working tree, verify current official APIs/model access and compatible dependency packaging, and establish the application acceptance harness and build/test commands. No application package, lockfile, runner or tests exist yet. Stage 0 remains Not started because upstream integration and the harness are build work.

## Decisions and upstream assumptions

Record date, decision, evidence/source and affected feature IDs. Explain compatibility changes explicitly. Do not silently change product scope or acceptance thresholds.

## Latest verification

Record exact commands, timestamp, commit/dirty state, report paths, result counts and remaining failures. Application verification: not run. Preparation checks are recorded below and do not count as release acceptance.

## Blockers

For each: affected implementation or validation, missing prerequisite, work completed meanwhile and exact unblocking step. Keep hardware absence separate from an unimplemented adapter.

## Resume note

At each milestone, leave the next concrete action and relevant paths. On resume, inspect the working tree and evidence before continuing; do not repeat completed work merely because chat context was shortened.

## Historical Revision 4 preparation record - 2026-09-10 America/Sao_Paulo (2026-09-11 UTC)

Preparation only was authorized. The supplied build goal has not been executed.

- Read READ_ME_FIRST.md, AGENTS.iago.md, the complete 718-line SPEC.md (three chunks), complete ACCEPTANCE.md, PROGRESS.md, GOAL.txt and all feature records.
- Copied all seven supplied files into the repository at their original relative paths. READ_ME_FIRST.md is retained at the root as the handoff guide. Created AGENTS.md from AGENTS.iago.md. Updated this progress record deliberately; preserved the specification, acceptance contract, feature manifest and goal unchanged.
- Verified exactly C1-C9, V1-V8, P1-P9 and D1-D6, without duplicate/missing/extra IDs, against section 2. All 32 implementation states remain not_started; all validation tiers remain not_run; implementation paths, test IDs and evidence remain empty.
- Verified SPEC.md SHA-256 against FEATURES.json: 20e8616fe00f21dff7211667b8e605b5bbc47023701b7212becdb0abb86dce82.
- All referenced handoff documents exist. Paths in SPEC.md section 15 describe future deliverables, not missing handoff files. External API links were not verified during preparation; verify them during the authorized build before relying on them.
- Added .python-version selecting the installed Python 3.12 line and .gitignore for local environments, generated Python files, private dotenv files, recordings and private fixtures. Non-secret dotenv examples remain trackable. Private runtime/fixture locations are conventions to use during implementation; no data was created.
- Created the empty .venv successfully using the exact command below; its interpreter reports Python 3.12.10 and sys.prefix differs from sys.base_prefix, confirming isolation. No dependencies are selected or installed during preparation. No system installation/update is needed for the inspected baseline; dependency-specific native requirements remain to be established during the build.

Commands used for baseline and prerequisites (read-only):

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git --version
python --version
py --list-paths
uv --version
node --version
npm --version
Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,OSArchitecture
Get-PnpDevice -PresentOnly | Where-Object { $_.Class -in @('Camera','Image','AudioEndpoint') } | Select-Object Class,Status,FriendlyName
Get-FileHash docs/iago/SPEC.md -Algorithm SHA256
```

Project-local environment creation:

```powershell
uv venv --python C:\Users\rafa\AppData\Local\Programs\Python\Python312\python.exe --no-python-downloads --offline .venv
```

### Prerequisites for later build and validation

| Affected work | Finding | Exact unblocking step |
| --- | --- | --- |
| Live OpenAI/Astra/STT/OpenAI voice | OPENAI_API_KEY absent in inspected environment scopes; billing and model access unverified | Supply a project API key through private local configuration, enable billing/access, and verify the named models with the implemented setup checks. Do not substitute the Astra brain silently. |
| Live ElevenLabs chosen voice | Key present but unvalidated; voice ID absent | Set Rafael's chosen ELEVENLABS_VOICE_ID privately and validate key, voice, model, format and streaming during setup. |
| Billable development and cost gates | Development budget not configured | Establish the budget amount/currency in the implemented local configuration before running billable validation. |
| Physical PC and perception qualification | Devices enumerated only; no app, recordings or labeled fixtures yet | Implement the harness, obtain browser device permissions, and collect the representative whiteboard/phone, wave/negative and acoustic evidence required by ACCEPTANCE.md. |
| Robot qualification | Reachy unavailable per handoff; no hardware probe performed | On arrival, configure hardware/firmware, pairing and media prerequisites, then execute the robot suite. Implement real adapters and fake contract tests independently during the build. |
| Dependency/API integration | No pyproject.toml, uv.lock, selected SDK versions or detector weights yet | Verify upstream contracts and Python 3.12 Windows/ARM compatibility, choose optional dependency groups and lock versions in build stage 0. Robot-only native requirements must stay optional for desktop. |

These are preparation findings and future validation prerequisites, not attempted suite results. No product scope decision blocks starting independent implementation after the user supplies the build goal.

### Historical Revision 4 preparation verification

Completed 2026-09-10 local time / 2026-09-11 UTC against baseline commit f51443cac531dc394e6ad0afa7b40878721d0a72. Preparation files remain untracked and uncommitted; README.md and Git configuration are unchanged.

- SHA-256 copy comparisons passed for the six unchanged handoff files; PROGRESS.md was intentionally updated. AGENTS.md matches AGENTS.iago.md byte for byte.
- PowerShell assertions passed for all eight repository handoff/instruction paths, the manifest specification hash, and all 32 unique feature IDs compared with both the required ranges and SPEC.md section 2 rows.
- All 32 implementation statuses are not_started, all tier statuses are not_run, and all implementation/test/evidence arrays remain empty.
- `.venv\Scripts\python.exe -c "import sys; print('Local environment:', sys.version.split()[0]); print('Isolated:', sys.prefix != sys.base_prefix)"` returned Python 3.12.10 and Isolated: True.
- `git check-ignore .venv/pyvenv.cfg .env .env.local recordings/sample.wav fixtures/private/sample.jpg` confirmed all five paths are ignored. `git check-ignore .env.example` returned 1 as expected: the example is not ignored.
- `git diff --exit-code -- README.md` returned 0. `git diff --no-index --check -- NUL docs/iago/PROGRESS.md` reported no whitespace errors; Git emitted only its existing LF-to-CRLF conversion warning. No Git settings were changed.
- `git status --short` listed only .gitignore, .python-version, AGENTS.iago.md, AGENTS.md, READ_ME_FIRST.md and docs/ as new preparation paths; .venv is ignored.
- `Get-Content -Raw docs/iago/GOAL.txt` retrieved the unchanged build goal for the user. It was not executed.

No application build, acceptance suite, provider validation, sensor capture or physical qualification was run. The results above establish preparation integrity only.


## Revision 5 planning amendment - 2026-09-11 UTC

User authorized planning/handoff changes only. No build goal, application implementation, dependency installation, provider calls or acceptance tests were executed. Earlier Revision 4 counts/hashes above are historical preparation evidence, not the current manifest.

- Baseline remains main at f51443cac531dc394e6ad0afa7b40878721d0a72. Existing untracked preparation files and ignored .venv were present before this amendment; preserve them. README.md, .gitignore, .python-version, .venv and Git configuration are unchanged.
- Current scope: 35 IDs, C1-C9, V1-V9, P1-P10, D1-D6 and K1. Original 32 feature records and statuses are preserved verbatim in meaning; new records are not_started with all validation tiers not_run and no implementation/test/evidence claims.
- Added P10 live-camera thumbs bound to one question, release rearming, ambiguity suppression and speech priority; V9 concurrent screen/camera, source lifecycle and global visual budgets; K1 incremental configured-project indexing, bounded cited retrieval and separate durable storage.
- Updated specification Revision 5, acceptance contract, manifest/hash, both instruction files, launch goal, installation guide, milestone scopes, interfaces, configuration/lifecycle plans and later scope. Keep using the amended repository files, not the original download.
- Recorded future wake word Iago (Aladdin parrot); no detector in this release.
- Routine defaults: thumb responses initially off; confidence 0.8, stable/release 350/300 ms, question TTL 15 seconds; suppress ambiguous multiple-person input. Screen starts off and has no audio. Document roots start empty; initial separate aggregate index cap 2 GiB, bounded local indexing and 10-second combined evidence deadline.
- Acceptance adds real thumb datasets, 12 screen scenarios, and a 3-project/30-supported-file document corpus plus 12 boundary fixtures, 45 answerable query variants and 10 absent questions. All original gates remain, with mixed workloads in the demo/soak.
- No unresolved material scope decision blocks independent implementation after the user issues the goal. Actual project roots, representative documents, provider access/voice/budget and physical fixtures remain local setup inputs. Parser/dependency compatibility, browser source availability and robot capability need verification during build, not an invented planning pass.
- Next action: await the amended GOAL.txt. Then establish the harness early, map all 35 IDs to runnable scenarios, and implement every milestone with honest per-tier evidence.

### Revision 5 preparation verification

Planning-integrity checks completed; these are not application acceptance results.

- Read-only Python assertions compared FEATURES.json with the extracted original manifest: all original 32 feature records are unchanged; P10, V9 and K1 bring the total to 35 unique IDs matching SPEC.md section 2. All original feature-table rows are preserved.
- All 35 implementation states remain not_started; all 110 validation-tier states remain not_run. No implementation paths, runnable test IDs or evidence were fabricated.
- All original eight acceptance behavioral gates remain present, with the requested source-scoped clearing amendment. Reviewed added datasets, deployment availability, lifecycle, cancellation, exclusions and later scope. Corrected an ambiguous later-scope sentence, clarified sharing-page versus audio-owner loss, and required citations/snippets for every document answer fact.
- All eight handoff/instruction paths exist; AGENTS.md and AGENTS.iago.md match. No application package, lockfile or test directory was created. Existing preparation tooling and README.md were preserved.
- Current SPEC.md SHA-256: 0422cfe9aea8e8ee5864a7c9097fb7edb38e05033588a6c24c92b4bd7013f7aa, matching FEATURES.json source_sha256. Historical Revision 4 hashes above remain historical only.
- GOAL.txt is one line, 2,359 characters including /goal and its final newline, below 4,000. Retrieved with Get-Content docs/iago/GOAL.txt; not executed.
- git diff --exit-code -- README.md returned 0. git status --short still lists only the pre-existing preparation paths as untracked: .gitignore, .python-version, AGENTS.iago.md, AGENTS.md, READ_ME_FIRST.md and docs/. No commit was made.

Await the user's build goal. Product implementation, automated evaluations, live-provider checks, live-PC evidence and robot qualification remain unstarted.

## Revision 6 preparation amendment - 2026-09-11 UTC

Planning only was authorized; no build goal was activated, application/harness implemented, accounts connected or acceptance suites run.

- Initial branch: main; commit: f51443cac531dc394e6ad0afa7b40878721d0a72. Initial dirty paths (all untracked): .gitignore, .python-version, AGENTS.iago.md, AGENTS.md, READ_ME_FIRST.md and docs/. Existing user work preserved; baseline copies for amendment review: C:/Users/rafa/AppData/Local/Temp/iago-prep-baseline-6b67544d16194d378b7abc4c22b32f9d.
- Inspected applicable root instructions and ancestor paths; no additional ancestor/docs AGENTS.md found. Read complete specification in chunks and all acceptance, tracker, progress, goal and guide contents. P10/V9/K1 additions and concrete datasets exist; no prior amendment is missing.
- Access probe used System.IO.FileMode.CreateNew for iago-access-probe-b56f7343b7d94797b54efac825a27a92.txt, read back exactly Iago preparation access probe, then Remove-Item -LiteralPath deleted it; Test-Path confirmed absence. Execution/read/write access succeeded; no permission policies changed. An amendment orchestration attempt failed before shell execution with SyntaxError: Unexpected identifier 'reachy_brain'; corrected tool-call quoting and proceeded with apply_patch. This was not an access-policy error.
- Added E1 once, preserving the previous 35 feature records and all acceptance gates/datasets. New scope is 36 features. All implementation states remain not_started and validation tiers not_run; no implementation paths, test IDs or evidence claims added.
- Synced eight existing planning/handoff files: SPEC.md, ACCEPTANCE.md, FEATURES.json, PROGRESS.md, GOAL.txt, root AGENTS.md, AGENTS.iago.md and READ_ME_FIRST.md. Architecture/module/configuration plans remain inside SPEC.md; no application files were created.
- Routine decisions: E1 section 15a avoids renumbering prior references; test-only two-account calendar exercises both real MCP transports and a direct adapter; initial bounded tool/journal defaults preserve stricter visual/document limits. P10 conversational thumbs retain their existing prohibition on external-action authorization. Production personal connectors remain later scope.
- Current specification SHA-256: f693ec33b066723946fcb777f2817da467fbd72165db1eb7af2bf0cad9324f8e. Historical Revision 4/5 hashes above remain historical.
- GOAL.txt objective: 2508 characters excluding /goal prefix and final newline; full saved command: 2515 characters. Measured programmatically before saving, below 4,000.
- Next step after the user issues GOAL.txt: re-inspect repository/evidence, verify APIs/SDKs/access, establish the acceptance entrypoint and implement every milestone. No unresolved preparation scope issue; credentials, fixtures and physical qualification remain future build prerequisites, not attempted check results.

### Revision 6 final preparation verification

Reviewed baseline-to-final diffs for all eight changed files (the existing files are untracked, so ordinary git diff alone cannot review them). Inline Python assertions confirmed 36 unique IDs matching SPEC.md section 2, unchanged previous 35 JSON records and feature-table rows, and byte-equivalent text for the entire prior acceptance behavioral/dataset block. All 36 implementation states remain not_started and all 114 validation-tier states remain not_run. Both AGENTS files match. No prior amendment is missing.

Executed `git diff --no-index --check -- <baseline-file> <current-file>` for each of the eight files against the baseline directory recorded above. All returned 1 with no whitespace diagnostics: files differ. The first preparation checker incorrectly required exit 0 and raised AssertionError; corrected it to accept difference exit 1 only with empty diagnostic output and no error/fatal message. Git's LF-to-CRLF warnings are informational; no Git settings or application acceptance criteria changed. `git diff --exit-code -- README.md` returned 0; `git status --short` retained the original untracked path list. Full diffs were inspected, including the new E1 requirements.

Exact final count/hash command (PowerShell; preparation integrity only):

```powershell
$manifest = Get-Content -Raw docs/iago/FEATURES.json | ConvertFrom-Json
$hash = (Get-FileHash docs/iago/SPEC.md -Algorithm SHA256).Hash.ToLowerInvariant()
if ($manifest.source_sha256 -ne $hash) { throw 'Specification hash mismatch' }
$goal = [System.IO.File]::ReadAllText((Join-Path (Get-Location) 'docs/iago/GOAL.txt'))
$objective = $goal.Substring(6).TrimEnd([char]10,[char]13)
if ($objective.Length -gt 4000) { throw 'Goal too long' }
Write-Output "Features=$($manifest.features.Count); Objective=$($objective.Length); Command=$($goal.Length); SHA256=$hash"
```

Observed: Features=36; Objective=2508; Command=2515; SHA256=f693ec33b066723946fcb777f2817da467fbd72165db1eb7af2bf0cad9324f8e. These are planning-integrity results only. No application build, test harness, acceptance pass, provider connection or build-goal activation occurred.
