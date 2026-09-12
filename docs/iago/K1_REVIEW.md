# Independent document-answer review

The live capture collects answers; it does not establish answer quality. K1-CITE and K1-ABSENT require independent human-authored labels and review under ACCEPTANCE.md. This scorer covers the fixed v1 corpus's 45 answerable variants and ten absent questions. Local retrieval, UI, spoken input and physical qualification remain separate.

Generate a blank template without overwriting an existing file:

```powershell
uv run python -m reachy_brain.evals.k1_review --capture local-data/evidence/stage-k1-astra-capture/live-provider-subset.json --output local-data/k1-human-review.json
```

Read the exact capture's `results[].measurements.answers`, the original corpus documents and questions. Independently author the expected answer facts for all 30 answerable questions and identify every supporting passage ID. Enter these in `labels.facts` and `labels.support_ids`. The support set must match the frozen corpus; if independent review finds an error there, correct and version the corpus transparently and recollect affected evidence rather than approving bad gold labels. Do not copy model guesses into expected facts.

Fill every grade for all 55 exact answer hashes. Do not alter capture or answer hashes. Set reviewer identity, review date and `independent_human_authored` only for actual independent human work. Templates contain null judgments and empty labels; they cannot pass. The software validates declarations and bindings, not a person's identity or honesty.

| Grade | Human judgment required |
| --- | --- |
| supported_correct | All required answer facts are supported and correct; for an absent question, the answer correctly reports insufficient evidence without inventing the requested fact. |
| abstained | The answer abstains from giving the requested facts. An answerable abstention cannot count as correct. |
| factual_claims | The response makes factual assertions, including incidental assertions during an abstention. |
| citations_complete | Every factual assertion has supporting citations and displayed snippets covering it. |
| citations_valid | Every citation resolves to the selected project, actual file/revision and truthful locator; snippets match the source. Check citations written in the answer as well as displayed evidence. |
| unsupported_assertions | Count every unsupported factual assertion; zero is required. |
| fabricated_citations | Count invented citations; zero is required. |
| explicit_absence | The answer explicitly reports absent/insufficient evidence for an absent question. |
| cross_project_leakage | The answer leaks facts or citations from another project. |
| rationale | Explain the fact-by-fact and citation review, omissions, errors and abstention assessment. |

The gate requires at least 41/45 supported-correct answerable variants and all ten absent cases correct with explicit absence. Any unsupported assertion, fabricated citation, cross-project leak, invalid citation or missing citation coverage for factual claims fails. Factual answers also need displayed evidence. A reviewer cannot classify a correct answerable response as having no factual claims to bypass that check.

Run scoring without new provider calls:

```powershell
$env:IAGO_K1_CAPTURE_REPORT='local-data/evidence/stage-k1-astra-capture/live-provider-subset.json'
$env:IAGO_K1_HUMAN_REVIEW='local-data/k1-human-review.json'
uv run --extra vision --extra robot iago-verify --suite live-provider --select k1_reviewed_quality --output local-data/evidence/k1-human-quality
```

Missing environment prerequisites yield blocked/non-success. Incomplete, duplicate, contradictory or hash-mismatched reviews fail validation; unsuccessful grades yield failure with reasons. Inputs are capped at 8 MiB each. Reports retain capture/review hashes, reviewer declaration and counts. A pass is scoped to those recorded answers and human judgments, not the current application's entire K1 feature or a fresh live run. No automated semantic or model self-grade substitutes for the independent review.

A fresh complete capture is retained in `local-data/evidence/stage-current-live-regression/live-provider.json`. Its blank, hash-bound review template is `local-data/evidence/stage-current-live-regression/review-template-4c791d85e53f-458dd2b9.json`. It contains 30 empty fact-label entries and 55 ungraded answers; it is not a review pass. Use this exact capture path with that template when configuring the scoring command above. Earlier captures/templates remain retained and must not be mixed with these answer hashes.

The latest shared-accounting capture is `local-data/evidence/stage-shared-accounting-live-regression/live-provider-subset.json`, SHA-256 `f4425b2d4ce97e69579d99b11af7ae9999c59b6cd7366f9cb2a65a81f0512f8a`. Its ungraded template is `local-data/evidence/stage-shared-accounting-live-regression/review-template-f4425b2d4ce9.json`. Use this pair together for independent review of the latest 55 answers; earlier pairs remain historical and valid only for their own exact answers. Capturing these answers passed; their quality has not been assessed.


## Bind a real PC query session

The live-provider capture above uses direct controller calls and synthetic playback. It cannot be relabeled as a physical UI session. For K1's separate PC requirement, collect all 55 query variants through the actual conversation UI, with at least ten actual spoken inputs. Preserve original query IDs, project assignments, displayed answers/evidence and their canonical answer hashes in the same capture schema consumed by score_review. Independently review those exact answers. Recording collection and human assessment are still required; the new validator neither drives the UI nor creates those records.

`reachy_brain.evals.k1_pc_bundle.K1PCBundle` binds six in-directory artifacts by relative `file` and exact SHA-256: capture, answer_review, plan, recording, session and ui_review. Capture and answer review are bounded to 8 MiB each; plan to 8 MiB; recording to 512 MiB (stream-hashed); session and UI review to 2 MiB each.

The session declares profile pc, origin reported-real, a finite duration up to 14,400 seconds, and exactly 55 unique queries covering the answer capture. Each query records id, answer_sha256, input_mode (typed/spoken), started and completed seconds relative to the session. Require 0 <= started < completed <= duration. At least ten distinct queries must be spoken. The complete frozen corpus and original answer-quality thresholds remain unchanged.

The UI review follows PCQueryReview: a reviewer identifier; artifacts mapping all five other artifact names to their exact hashes; frozen_at, recorded_at and reviewed_at timestamps; and reviewed_query_ids containing each query exactly once. Freeze precedes recording and review follows the full session duration. Required true judgments are independent_human_authored, recording_origin_and_alignment_reviewed, all_queries_through_real_ui_reviewed, spoken_input_and_transcription_reviewed, displayed_answers_and_snippets_reviewed, project_configuration_selection_and_progress_reviewed, reindex_errors_and_removal_reviewed, originals_unchanged_on_removal_reviewed and durable_index_and_transient_context_distinction_reviewed. Supply these only after actual independent review; a boolean does not authenticate a human action or hardware.

Set private IAGO_PC_K1_MANIFEST to a JSON manifest with fixture_kind physical-pc-independent-recording, profile pc, nonempty operator/application_revision/devices metadata and bundle matching K1PCBundle. Manifest limit: 64 KiB. Run:

```powershell
uv run --extra vision --extra robot --extra robot-camera iago-verify --suite live-pc --select 'test_physical_k1' --output local-data/evidence/reviewed-pc-documents
```

PC-K1-REVIEWED-QUERY-BUNDLE records exact manifest and artifact hashes, query counts and answer-quality results. Missing manifest is blocked; invalid records, insufficient spoken queries or failed answer grades fail. Successful record checks return review_required with acceptance_pass and physical_qualification false. Reviewer names and free-form judgments remain in private artifacts. This checks declared evidence bindings, not recording provenance, physical playback, the full mixed workload, mutation timing or robot qualification.

Implementation evidence: stage-k1-pc-bundle-reviewed/offline-subset.json passed 34 synthetic bundle/entrypoint/existing-answer-scoring checks. Stage-k1-pc-prerequisites/live-pc-subset.json selected one blocked check for the absent manifest. No new provider or device calls, real UI capture or independent judgments were produced by these checks.
