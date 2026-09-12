# Screen grounding evaluation component

ACCEPTANCE.md V9-GROUNDING remains authoritative. The scorer in `reachy_brain/evals/screen_grounding.py` implements a bounded comparison component, not a live capture harness. Its synthetic tests cannot qualify screen sharing, picker permissions, real Astra grounding or physical readability. Every output includes `physical_capture_verified: false`, even when the scored checks pass.

Run against private local records:

To extract saved answers first, enable opt-in transcript recording during the real session and use Export JSON. Create `mapping.json` with `plan_sha256` (the canonical validated ScreenPlan digest), `transcript_sha256` (SHA256 of the exact exported bytes), and `entries` mapping every case ID to a distinct saved assistant entry ID. Then run:

```powershell
uv run python -m reachy_brain.evals.visual_capture --kind screen --plan local-data/screen/plan.json --mapping local-data/screen/mapping.json --transcript local-data/screen/session.json --output local-data/screen/extracted.json
```

This writes an intermediate record, not a ScreenCapture or a passing evaluation. It copies saved heard text and exact cited frame/source/time/hash references, preserving original metadata separately for interruption/crop review. Generated-but-unheard continuation is not substituted for the answer. Readings remain empty for human extraction; no model identity, fixture-freeze chronology or physical execution is invented. Missing/truncated evidence, unknown capture times, reused answers, altered exports and incomplete case mappings are rejected. Existing output files cannot be overwritten. The default `--kind visual` still serves whiteboard/phone plans.

Complete the capture and independent review using actual run evidence before invoking the scorer below. Do not fill model identity or physical attestations from the extractor's successful exit status.

The verification entrypoint also checks a complete reviewed bundle. Set `IAGO_PC_SCREEN_FIXTURES` to the private directory containing `plan.json`, `capture.json`, `review.json`, `mapping.json`, `session.json` and an `images/` directory of retained artifacts, then run:

```powershell
uv run --extra vision --extra robot iago-verify --suite live-pc --select test_reviewed_screen_evidence_bundle --output local-data/evidence/screen-reviewed
```

This selector requires every captured answer's text and ordered citations to match the bound transcript extraction exactly; independently extracted readings remain separately scored. It verifies retained image bytes and runs every existing screen-scoring assertion. Missing configuration/files are blocked; malformed, changed or failing supplied records fail. It reports canonical record hashes and measurements without copying answer text. A passing bundle check establishes recorded-evidence consistency and reviewed scores only. Actual picker execution, frozen-target chronology and model request linkage still require the independent audit described below; the nested scorer retains `physical_capture_verified: false`. Do not use a synthetic bundle to claim a physical pass.

```powershell
uv run python -m reachy_brain.evals.screen_grounding --plan local-data/screen/plan.json --capture local-data/screen/capture.json --review local-data/screen/review.json --output local-data/screen/score.json
```

Add `--image-root local-data/screen/images` to verify saved retained originals. Name each JPEG `<image_sha256>.jpg` using its byte-content SHA256 from the visual reference. The command requires every expected or cited image, resolves paths inside that directory, hashes and decodes the same bounded bytes, and rejects missing, changed, unsupported-format or oversized artifacts. Limits are 64 MiB per image, 512 MiB total, 20 million pixels and 8192 pixels on either dimension. Files are processed serially. Keep this directory private and out of Git. Exporting artifacts is explicit; ordinary transcript saving does not preserve images.

Use History's Inspect image → Save image action to download the retained original with the matching hash filename, then place that explicit export in the private artifact directory. Save required artifacts while their frames are available; hashes cannot recover expired images. This export does not create a full capture manifest or prove fixture freeze time.

The score separately reports `image_artifacts_verified`. Without `--image-root` it is false. Successful artifact verification sets it true but leaves `physical_capture_verified` false: byte integrity cannot attest browser sharing, fixture freeze time or factual grounding. No raw image, answer text or filesystem path is added to the score output.

Input files are limited to 1 MiB each and validated against the strict Pydantic schemas in that module. The output must not already exist. Exit 0 means only the component's scored checks passed; 2 means a measured gate failed. Malformed, incomplete or mismatched records raise an error. Reports contain counts and canonical record hashes, excluding raw answer text and reviewer identity.

The three schemas separate expectations, captured answers and independent review:

- `ScreenPlan`: version 1; unique case IDs, categories, picker choices, expected references and globally unique exact-label IDs. At least 12 positive cases use the required 3 current, 1 each near minutes 1/5/9, 2 crop, 2 camera-confusion, 1 pin-expiry and 1 source-change categories. Separate blank and unreadable cases are additional. At least 20 labels are required. Account for monitor/window/tab using tested choices or explicit unavailability reasons, never both for a choice.
- `ScreenCapture`: canonical plan hash, actual model name and one answer per case. Each answer includes text, cited frame/source/time/image-hash records and verbatim spans keyed by frozen label ID. Missing readings count as incorrect. An extracted span that does not occur in the answer is rejected. These records must come from actual evidence when used in a physical assessment.
- `ScreenReview`: canonical capture hash, reviewer identity, independent-human-review attestation and complete per-case verdicts for scenario execution and invented text. Blank/unreadable invented text fails independently of the label accuracy score. Execution review must inspect the actual scenario, including simultaneous camera similarity, reference ages, pin expiry, source changes and picker availability.

Use `digest(validated_record)` for canonical hashes; it normalizes JSON key order and whitespace. Replacing answers invalidates review. Freeze human-authored labels, intended sources/times and scoring before the scored run. The current reference comparator expects resolved runtime frame IDs, timestamps and image hashes. A future capture harness must bind those runtime identities to the predeclared reference targets using recorded capture events, preserving the original frozen targets. Do not rewrite expectations to match an answer or claim that a final plan hash proves when labels were frozen.

Scoring reports exact-label accuracy separately from reference selection and provenance. The label threshold uses integer comparison for at least 90%, including 18/20. Every case must select its expected reference set; every citation must match the expected frame, source, timestamp and image hash. No missing-case or missing-citation vacuous pass is possible. Extra unrelated citations fail. Human review is essential for whole-answer meaning; verbatim label extraction alone does not establish factual support or useful reasoning.

Remaining work: capture the actual browser picker and conversation session, independently verify origin and provenance against retained artifacts, preserve freeze-time evidence, and record actual Astra request/answer linkage. The live-PC bundle entrypoint checks supplied records but does not establish these facts on its own. Whiteboard/phone reasoning and uncertainty evaluation, V9 lifecycle/cancellation, assisted robot sharing and physical source controls remain separate requirements. No physical pass has been recorded by this component.

The application retains capture time, capture-time-known status, source kind, crop coordinates and the retained image's SHA256 in opt-in transcript visual references when available. Overview and different crops of the same frame remain distinct within the existing 32-reference limit. This metadata survives normal image expiry without persisting pixels. The hash identifies the full-resolution stored JPEG/PNG after normalization/re-encoding, not the originally uploaded file, thumbnail or crop bytes. Preserve an explicit private export of that retained JPEG/PNG when independently checking the hash; a hash cannot recover expired pixels. Crop coordinates identify the region of that parent image. These fields do not prove label freeze time or correct model grounding.

New opt-in answers now carry bounded `model_responses` metadata from the Astra adapter: response IDs, requested model, and provider-reported model when present. Tool-round and answer-round IDs are retained separately (up to 32, with `model_responses_truncated` on overflow). The extractor preserves these in original provenance metadata. Missing fields remain missing; older captures are not retroactively attributed. The reviewed-bundle entrypoint now requires this metadata for every answer: complete requested and reported models matching the declared Astra capture, nonempty response IDs with no reuse across cases, and no truncation. The standalone record scorer still checks only its declared records. Configuration alone cannot attest the returned model or physical origin. Summary-model calls are not recorded as answer responses.

Model metadata checks establish consistency with the saved export, not authenticity of a manually altered file. Preserve independent run evidence for audit. Old exports with absent provider-reported model or response metadata remain insufficient for the reviewed-bundle gate; do not fill those fields from configuration. Intermediate transcript extraction remains available for inspecting such exports without calling them qualified.
