# Whiteboard and phone evaluation

SPEC.md Revision 6 and ACCEPTANCE.md gate 4 require at least ten representative whiteboard/phone scenarios. The local scorer now reports exact reading, reference selection, uncertainty and reviewed reasoning separately. It does not capture physical material, establish camera origin or provide an aggregate acceptance pass. No numeric reading threshold is invented for this gate.

Keep private artifacts under ignored `local-data/`. Prepare a `VisualPlan` JSON using the strict schemas in `reachy_brain/evals/visual_grounding.py`. Include both media and all scenarios: current image, show then lower, board before/after, references near minutes 1/5/9, fine-text crop, blur, glare, too-small text, upload fallback and expiry. Cases may cover multiple scenarios, but at least ten distinct case IDs are required. Freeze exact labels and expected frame ID/source ID/capture time/stored-image SHA-256 references before the scored run; retain tuning material separately. Expired cases expect no available references or readable labels.

Record the actual Astra answers and inspected references in `VisualCapture`, bound to the canonical plan digest. A human reviewer extracts verbatim answer spans for the frozen reading label IDs; omitted labels count as incorrect. `VisualReview` binds to the capture digest and records scenario execution, appropriate uncertainty, absence of invented readings, and a reasoned usefulness assessment for every case. An LLM cannot supply the independent human review. Keep the full review privately; the score report contains categories and counts, not answer text or personal reviewer identity.

Each positive scenario must include at least one readable case with frozen labels and no expected uncertainty. Both whiteboard and phone material must have readable cases. Combining a positive scenario with a blur/expiry tag cannot remove that requirement. Reports include scenario and medium counts so dataset coverage remains visible alongside the separate measurements.

For application capture, enable saved transcripts before the session and use its **Export JSON** button afterward. The authenticated `/api/transcripts/{session}/export?format=json` response has version 1, the session ID and recorded entries with stable entry IDs, timestamps and metadata. Assistant `text` is the heard portion; `metadata.generated_text` is separate and may include unheard output. Preserve interruption/truncation flags when selecting scored answers. `metadata.evidence_refs` carries the actual recorded references, including available image hashes, capture times and crop regions. Missing or truncated provenance must be resolved or reported incomplete, never filled from expected labels. Export corresponding images through the existing image controls before expiry. This export does not automatically map turns to frozen case IDs, establish model access, create human readings or prove physical scenario execution; those capture steps remain required.

Store exact retained JPEG/PNG images as `<sha256>.jpg` or `<sha256>.png` under the image directory. Run:

To extract recorded answers, create a `CaseMapping` JSON with `plan_sha256` (canonical `digest(plan)`), `transcript_sha256` (SHA-256 of the exact exported file bytes), and `entries` mapping every case ID to a distinct saved assistant entry ID. Run:

```powershell
uv run python -m reachy_brain.evals.visual_capture --plan local-data/visual/plan.json --mapping local-data/visual/mapping.json --transcript local-data/visual/session.json --output local-data/visual/extracted.json
```

The extractor copies heard answers and inspected references, with original metadata retained separately for crop/interruption review. It rejects missing/truncated evidence, uncertain capture times, reused entries and mismatched hashes. Empty reading maps require human extraction. This intermediate file deliberately is not a completed `VisualCapture`: establish the actual model and capture start from independent run evidence, then populate the capture metadata and obtain review. Preserve the extraction and source export as provenance. Inputs are bounded (32 MiB transcript, 1 MiB plan/mapping), and output is exclusive-create. No model, expected reading or physical success is filled in automatically.

Score the completed capture and review:

```powershell
uv run python -m reachy_brain.evals.visual_grounding --plan local-data/visual/plan.json --capture local-data/visual/capture.json --review local-data/visual/review.json --image-root local-data/visual/images --output local-data/visual/score.json
```

Inputs are limited to 1 MiB each. The command checks record binding, declared freeze-before-capture chronology, coverage, exact reference equality, verbatim reading spans and actual bounded JPEG/PNG artifact hashes. It refuses to overwrite an existing report. Exit zero means report generation succeeded, even when measurements show poor performance; inspect all separate results. `physical_capture_verified` and `release_validated` remain false. Hashes and declared timestamps alone do not prove that labels were frozen before capture, that the pictured material was physically shown, or that the reviewer is independent. Retain independently verifiable capture chronology and operator evidence for those claims.

Remaining work includes actual application scenario capture, physical board/phone fixtures, independent assessment and the thirty-minute demonstration. Synthetic scorer tests establish only record validation and scoring behavior. Screen-sharing qualification remains separately defined in SCREEN_EVAL.md and cannot substitute for this dataset.

The live-PC reviewed-bundle entrypoint uses `IAGO_PC_VISUAL_FIXTURES` pointing to private `plan.json`, `capture.json`, `review.json`, `mapping.json`, `session.json` and `images/` artifacts. Run:

```powershell
uv run --extra vision --extra robot iago-verify --suite live-pc --select test_reviewed_visual_evidence_bundle --output local-data/evidence/visual-reviewed
```

This validates VisualPlan/VisualCapture/VisualReview, exact transcript text and ordered citations, complete matching recorded Astra model provenance, bounded retained images and the existing scenario/chronology/review requirements. All scenarios must have reviewed execution. Missing prerequisites are blocked, altered or invalid records fail. Reading, reference selection, uncertainty and reasoning remain separate measurements; successful evidence processing does not invent an aggregate quality threshold or qualify physical vision. `physical_capture_verified` and `release_validated` remain false in the score. Independent origin and freeze chronology still need audit. The mapping format is shared with visual_capture.py; ScreenPlan and VisualCapture cannot be mixed.

New saved visual references also retain capture_uncertainty_seconds and capture_interval when the input evidence carries timing metadata. Null denotes missing or invalid bounds, not zero uncertainty. The controller derives a finite interval only from a valid capture timestamp, nonnegative bound and known capture time. Old exports can omit these fields; do not interpret their absence as calibrated precision. Extraction retains original metadata for review; current exact-reference scoring does not by itself qualify uncertainty-expanded physical timing.
