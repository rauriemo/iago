# K1 candidate corpus v1

This is a synthetic, non-secret evaluation corpus authored by Codex. It is a **candidate**, not the human-authored gold-label dataset required for full K1 qualification. Review the documents, facts, logical support IDs, query variants and absent-answer labels before using it to claim that gate. No model self-grading supplies these expected facts.

The frozen JSON sources contain three projects with ten supported files each: two Markdown, two TXT, two three-page text PDFs, two DOCX files with three named sections, and two source/configuration files per project. Python, JavaScript, JSON, YAML and TOML are represented. Projects reuse relative filenames and vocabulary while contradicting launch, delivery, hardware and configuration facts.

There are 30 answerable questions, 45 variants (15 paraphrases), six questions requiring two passages, and ten absent-answer questions including four with answers only in another configured project. Logical support IDs identify file and locator; actual index citation IDs additionally bind the materialized file revision. Source hashes in `manifest.json` are checked before materialization. Do not edit labels after observing scores without recording the correction and issuing a new corpus version.

Materialize into a new directory from the repository root:

```powershell
uv run --extra vision --extra robot python -m reachy_brain.evals.k1_corpus --output local-data/fixtures/k1-v1
```

The builder refuses to overwrite an existing directory. It adds 12 boundary fixtures: two raster-only PDFs, a mixed text/raster PDF, malformed DOCX, encrypted PDF, unsupported binary, two synthetic credential files, two generated/dependency artifacts, an outside-root directory link and a traversal path. Link targets remain inside the generated fixture parent, outside the configured project. Windows uses a junction if symbolic-link creation is unavailable. No real credentials or user files are used.

Run the candidate parser, boundary, retrieval and citation checks with:

```powershell
uv run --extra vision --extra robot iago-verify --suite offline --select k1_corpus --output local-data/evidence/k1-candidate-v1
```

These tests use the real parser subprocesses, project index and revision checks. Retrieval requires all labeled support in the top eight, with the unchanged 41/45 and 14/15 thresholds. Per-query results are recorded in the report. They do not evaluate Astra answers, absent-answer behavior, all mutations/cancellation cases, the real UI or physical/spoken queries. Those checks remain required, as does human review of the labels.


The mutation and worker-race checks run separately:

```powershell
uv run --extra vision --extra robot iago-verify --suite offline --select "k1_mutations or k1_worker_races" --output local-data/evidence/k1-update
```

They make three edits, three moves, three deletions and three creations across generated projects through the running application's background indexer. Old citations must fail immediately; stable changes must become visible within five seconds. Recorded measurements are actual local filesystem/HTTP timings, not physical UI or hardware qualification. The watcher scans at one-second intervals and coalesces work while a project worker is active. Larger folders and the mixed-workload soak still require measured resource qualification.

Separate held-worker cases cover removal/cancellation during parsing and project selection changes during search or passage verification. The mutation run also checks durable restart and preserves all fixture files outside its declared changes. Astra answer correctness, absent answers, human gold-label review, real UI and spoken-query checks remain separate.


`--select k1_browser` exercises all 45 candidate query variants through real headless Chromium and the application's folder, active-project and search controls. It checks visible facts/locators, file-level errors, Reindex, Remove index, original preservation and a deliberately delayed response after a project switch. A synthetic evidence event also checks the actual control transport and conversation-snippet rendering. This remains offline browser evidence; no actual Astra answer, acoustic/spoken input or physical device is represented by that run.

Actual Astra answer capture is a separate billable run:

The independent review template and runnable quality gate are documented in [K1_REVIEW.md](../../../docs/iago/K1_REVIEW.md). Missing review inputs are blocked; unfilled templates never pass.

```powershell
uv run --extra vision --extra robot iago-verify --suite live-provider --select live_k1_answers --output local-data/evidence/k1-astra-capture
```

It runs all 45 answerable variants and ten absent-answer questions through Conversation, the configured Astra adapter and the registered document tools, starting fresh conversation context for each question. The synthetic speech sink avoids speech charges and provides no acoustic evidence. Provider plan limits stop further cases. The report retains completed answers, displayed document evidence, corpus manifest, per-answer hashes, elapsed times and provider-attempt count. Returned evidence is checked against the selected project and current revision through the real index.

A passing capture establishes successful collection and evidence identity only. Its quality_status remains not_assessed. Independent human review must validate the candidate gold labels and score all answer facts, citation coverage/truthfulness, unsupported assertions and absent-answer behavior under the unchanged ACCEPTANCE.md thresholds. An abstention on an answerable query remains incorrect for supported-correctness scoring. Missing citations are failures for factual answers; merely receiving some valid snippets is insufficient. The capture does not parse or certify free-text citations, semantically grade answers, exercise spoken queries or qualify physical UI/audio. Review must bind to the exact recorded answer hashes; model self-grading is not sufficient.
