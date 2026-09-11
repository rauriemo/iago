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
