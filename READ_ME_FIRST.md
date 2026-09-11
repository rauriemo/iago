# Delegating the full Iago build to Codex

Prepared 11 September 2026. This guide accompanies the Iago handoff ZIP; it does not contain an implemented app or claim any acceptance results.

Use the local Codex CLI's native `/goal` with the full specification and an acceptance contract in the repository. This fits the workflow you already use. Current Codex documentation supports setting, editing, pausing and resuming a goal; goal text is limited to 4,000 characters, so reference the longer files instead of pasting the architecture into the command. [CLI commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli)

One assignment should cover the complete first release. Let Codex use incremental milestones inside that assignment, running and fixing relevant tests as it goes. Define completion through observable behavior and evidence. OpenAI's long-running-work guidance similarly recommends explicit outcomes, constraints and verification criteria, kept in the same session. [Long-running work](https://learn.chatgpt.com/docs/long-running-work)

## Prepared checkout and Revision 6 amendment

The extracted handoff is already installed in this repository; preserve the existing preparation tooling and progress record. Do not recopy the original Revision 4 download over these amended files. The active release contains 36 features: all 35 Revision 5 features (including P10 live-camera thumb responses, V9 continuous desktop sharing and K1 project-document search) plus E1 extensible integrations and workflows. Review the amended specification and acceptance datasets before issuing GOAL.txt. Editing these planning files does not start the build.

Configure project folders and the active project locally during implemented setup; use non-secret representative document fixtures for evaluation. PC screen capture requires an explicit browser sharing session and stays visual-only. The screen and camera share existing visual storage limits, while document indexes have a separate visible budget and removal control. PC sources are not automatically available on standalone Reachy.

No wake word is required or implemented. Keep conversation-start controls and visual triggers. If wake-word support is added later, its selected word is Iago, named after the Aladdin parrot. Required credentials, chosen voice, development budget and physical evidence remain setup/qualification items.

E1 requires working shared tool execution, actual MCP transports, direct adapters, trusted on-demand workflows, account policies, exact confirmations and durable action reconciliation. The build must document module/tool/workflow registration and provide a test-only fake-data calendar plus sample workflow. No real calendar, email or WhatsApp accounts are connected in V1; production connectors, OAuth setup screens, webhooks and notification/scheduling services remain later work. Local audio interruption stays independent of integrations. See SPEC.md section 15a and ACCEPTANCE.md E1 gates; no infrastructure or connector tests have run during preparation.

## Original handoff installation reference

1. Extract the ZIP into a temporary folder. Copy `docs/iago/` and `AGENTS.iago.md` into the root of your existing `projects/iago` checkout. Preserve any existing files at those paths; merge deliberately if a name collides. The ZIP does not contain a Git repository or a replacement application template.
2. If your repo has no `AGENTS.md`, copy `AGENTS.iago.md` to that name. If it already has one, merge the Iago instructions into it. Keep existing applicable project guidance. The supplied goal also explicitly reads `AGENTS.iago.md`.
3. Review SPEC.md and ACCEPTANCE.md once, particularly the scope and validation targets. Sending the supplied build goal is the instruction to start implementing that scope.
4. Install or update the local CLI using the official installation guide, then launch it in the actual checkout. This chat cannot access your PC's local folder. [Codex CLI installation and local operation](https://learn.chatgpt.com/docs/codex/cli)

For a checkout under your Windows home directory, use PowerShell:

```powershell
Set-Location "$HOME\projects\iago"
codex
```

If the folder is elsewhere, replace the path with its actual absolute path. Use the local Windows environment for device validation; a cloud or Linux-only run cannot establish that the native Windows microphone/speaker experience works.

Codex reads AGENTS.md automatically and discovers layered project instructions. Keep it short and point to the specification: the default combined instruction discovery limit is 32 KiB, while this specification is larger. [AGENTS.md guidance](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

## Configure the session, then issue one goal

Use `/model` to select your available capable coding model and a reasoning effort suitable for this large build. My recommendation is a high effort setting for implementation and difficult debugging. That choice is independent of the app's Astra brain and its initial low reasoning setting. Use `/permissions` to select the available policy that permits project edits and routine development commands, and `/status` to check the directory and configuration. Goal mode does not expand the session's access. [Session commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli), [Goal access boundaries](https://learn.chatgpt.com/docs/long-running-work)

If you want to bring over selected Claude Code setup, `/import` is available in the local CLI before a task starts. Review what you import so unrelated project instructions do not enter Iago. This is optional; the handoff files are sufficient. [Import command](https://learn.chatgpt.com/docs/developer-commands?surface=cli)

Paste the entire line from `docs/iago/GOAL.txt` into Codex. It authorizes implementation, references all the supporting files, and asks Codex to continue through all milestones without asking whether to proceed. `/plan` is useful if you still want to resolve scope; once you accept this specification, a second long planning conversation is unnecessary.

Set provider credentials and your ElevenLabs voice through local environment/configuration when requested by the implemented setup flow. Do not paste secrets into the goal or committed documents. Establish the live development budget in local configuration. Missing access should block only the affected checks while Codex continues all independent work.

## What the package does

| File | Purpose |
| --- | --- |
| `docs/iago/SPEC.md` | Revision 6 amended architecture and full feature scope |
| `AGENTS.iago.md` | Concise working instructions to merge into AGENTS.md |
| `docs/iago/GOAL.txt` | Ready-to-paste build goal |
| `docs/iago/ACCEPTANCE.md` | Behavior, evidence, verification runner and completion contract |
| `docs/iago/FEATURES.json` | All 36 feature IDs with separate implementation and validation states |
| `docs/iago/PROGRESS.md` | Milestones, decisions, commands, blockers and resumption notes |

The acceptance harness is part of the build assignment. The JSON manifest and Markdown checklists do not themselves test the app. Codex must turn them into runnable checks and record actual results.

## How to get dependable completion

- Build and verify a real PC audio slice early. It exposes API, streaming, echo and interruption issues before they spread through the whole app. Then continue through camera/screen vision, project-document retrieval, waves/thumb responses, initiative, E1 infrastructure/workflows and robot integration; all remain in scope.
- Use deterministic assertions for cancellation races, retention, trigger policy and transport. Use representative real media and model calls for perception and visual reasoning. Use physical recordings for speaker cutoff. An LLM grading its own prose cannot prove these behaviors.
- Keep a small final set of your own whiteboard/phone examples separate from tuning examples. Judge exact reading, selected visual evidence and brainstorming usefulness separately. Codex can prepare collection and evaluation tools; your room and materials supply the final product evidence.
- Keep feature IDs, assertions and thresholds stable. Fix tests when they are wrong, with an explanation; do not quietly weaken them. A mocked provider, skipped test or absent robot never counts as a live pass.
- Have Codex update the feature matrix and progress record after meaningful milestones. Ask for the current failing gates or blockers if you want status; the goal can keep working in the same session.
- Finish with a dedicated review against the full spec and a regression run after fixes. The CLI supports `/review` for a review of local changes. If you use it after the goal, feed material findings back into the same task for correction. [Local review](https://learn.chatgpt.com/docs/codex/cli)

Use `/goal pause` and `/goal resume` inside the session. If you close the CLI, reopen the saved session with `codex resume`, inspect the current goal and resume it if paused. Keep the PC awake for active local work. A persistent goal still depends on the running environment, available quota, access and necessary human decisions. [Goal commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli), [CLI session resumption](https://learn.chatgpt.com/docs/codex/cli)

For this first build, I recommend the interactive local CLI. `codex exec` is useful later for scripted jobs or CI, but adding an external retry loop now is unnecessary. [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)

The intended handoff is a full implementation with reproducible results and explicit remaining physical qualification. A single delegated assignment can pursue that outcome; no prompt can establish robot acoustics or onboard performance before the robot exists. Your eval discipline is the right foundation—make the evidence, rather than the agent's completion message, determine what is done.
