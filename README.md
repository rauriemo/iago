# Iago — a Reachy Mini brain

Iago is a voice-first desk assistant: a native Windows application that talks, listens, watches and remembers, designed to later inhabit a [Reachy Mini](https://www.pollen-robotics.com/) robot. It brainstorms out loud with you, reads your whiteboard and phone screen through a camera, watches your desktop when you share it, answers questions about your project documents, and responds to physical gestures like waves and thumbs-up — all while staying interruptible the instant you speak.

> **Project status: specification complete, implementation not started.**
> This repository currently contains the full V1 specification (Revision 6), acceptance contract, feature tracker and build goal. No application code exists yet and no acceptance results have been recorded. Everything described below is *planned* scope unless explicitly marked otherwise.

## What V1 will do

The first release covers **36 features** across six areas (IDs from `docs/iago/FEATURES.json`):

| Area | Features | Summary |
| --- | --- | --- |
| Conversation & voice | C1–C9 | Spoken brainstorming with full-duplex interruption, patient turn-taking, editable personality, heard-answer conversation history, and optional saved notes |
| Vision & visual memory | V1–V9 | Live camera view, whiteboard/phone reading, a rolling ~10-minute visual ring buffer, timeline search, pinned reference images, evidence display, and continuous visual-only desktop sharing |
| Perception & initiative | P1–P10 | Person presence, object detection, temporal wave recognition, thumbs-up/down answers bound to live questions, editable trigger rules, greeting presets, aware mode, and strict restraint policies |
| Deployment & controls | D1–D6 | PC-only mode, PC-assisted Reachy, standalone Reachy profile, control UI, recovery behavior, and diagnostics/budget reporting |
| Project knowledge | K1 | Read-only indexing and cited search of configured project folders during conversation |
| Extensibility | E1 | Shared tool registry/executor, MCP (stdio + Streamable HTTP) and direct adapters, credential-provider interfaces, trusted on-demand skills, per-tool/account action policies, and durable action reconciliation |

Explicitly **out of V1 scope**: wake-word detection (the future word is "Iago", after the Aladdin parrot), and real calendar/email/WhatsApp connectors — E1 ships infrastructure plus a test-only fake-data calendar module and sample workflow only.

## Architecture at a glance

- **Brain:** Astra handles reasoning over text and images, with bounded visual-retrieval and document-search tools.
- **Speech:** ElevenLabs with a configured voice is preferred when valid; OpenAI speech is the default and fallback. Recognition, reasoning and synthesis are separate stages.
- **Interruption:** audio stopping is a local, direct path — independent of providers, integrations and skill execution. Interrupted answers never contaminate history with unheard facts.
- **Vision:** camera and shared desktop feed a bounded visual history (time- and byte-limited) with explicit pins; archive and detector rates are independent.
- **Perception:** a small local detector plus temporal state machines for waves and thumbs; initiative is governed by cooldowns, quiet times and user-speech priority.
- **Deployment:** native Windows with real browser media devices; robot dependencies are optional. On Reachy, the robot owns physical media and local stop supervision while the PC orchestrates.

Full detail lives in `docs/iago/SPEC.md` (Revision 6, ~113 KB, 22 sections).

## Repository guide

| Path | Purpose |
| --- | --- |
| `docs/iago/SPEC.md` | The complete Revision 6 architecture and feature specification |
| `docs/iago/ACCEPTANCE.md` | Behavioral acceptance contract, evidence requirements and verification entrypoint |
| `docs/iago/FEATURES.json` | All 36 feature IDs with separate implementation and validation states |
| `docs/iago/PROGRESS.md` | Milestone log: decisions, commands, blockers and resumption notes |
| `docs/iago/GOAL.txt` | The single build-goal command that authorizes implementation |
| `AGENTS.md` / `AGENTS.iago.md` | Working instructions for coding agents |
| `READ_ME_FIRST.md` | Human guide for delegating the build to a coding agent |

## For coding agents

If you are an agent working in this repository: read `AGENTS.md` first, then `docs/iago/SPEC.md`, `docs/iago/ACCEPTANCE.md`, `docs/iago/FEATURES.json` and `docs/iago/PROGRESS.md` before writing code. Key rules:

- The build is authorized only by the goal in `docs/iago/GOAL.txt`; planning edits do not start it.
- Implementation state and validation state are tracked separately. Missing credentials, fixtures or hardware make a check `blocked`, never `pass`. Mocked providers and synthetic media must be labeled and never count as live or physical passes.
- Acceptance gates, datasets and thresholds are fixed — fix wrong tests transparently, never weaken them.
- Credentials come from local configuration and never enter model context, logs, browser bundles or Git.

## Data and privacy

Visual history is bounded and evicted by both time and size; pins are explicit; screen sharing is visual-only and source-scoped clearing is supported. Project-document indexes are local, read-only over the originals, and fully removable. Transcripts are saved only when enabled, with export and delete controls. Personal recordings stay out of Git by default.

## Verified functionality

None yet. The acceptance harness is part of the build assignment; once implementation begins, `docs/iago/FEATURES.json` and `docs/iago/PROGRESS.md` will carry the real per-feature implementation and validation status with evidence paths. Until then, treat every capability on this page as a specification, not a claim.
