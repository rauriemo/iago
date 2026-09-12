# Interruption opening words

ACCEPTANCE.md gate 3 requires preserving the user's opening words during at least 20 spoken interruptions. Cutoff latency, opening-word recognition and the ten-minute zero-self-trigger observation are separate requirements.

Before a scored run, freeze an `OpeningPlan` JSON using the schemas in `reachy_brain/evals/opening_words.py`: version 1 and at least 20 unique cases, each with ID, expected opening phrase and provider (`openai`, `elevenlabs`, `fallback`). Include both voices and actual fallback. Keep prompts, labels and tuning material separate. The phrase must contain words; do not shorten it after observing a failure.

Enable opt-in transcript saving and export the actual session as JSON. Build `OpeningMapping` with the canonical `digest(plan)`, SHA-256 of the exact exported bytes and an `entries` map from every case ID to a distinct saved user speech entry. Retain missing attempts in this map; an absent entry scores as missing recognition. Typed text cannot substitute for speech recognition. Preserve independent recordings and reviewed alignment that establish what was actually said, overlap with assistant playback, the observed provider and pre-run label freezing. The scorer cannot establish those facts from declarations or transcript hashes.

```powershell
uv run python -m reachy_brain.evals.opening_words --plan local-data/openings/plan.json --mapping local-data/openings/mapping.json --transcript local-data/openings/session.json --output local-data/openings/result.json
```

The command compares expected words with the beginning of actual recognized text. It ignores case and punctuation separators and normalizes curly apostrophes; it does not remove accents, accept paraphrases or search later in an utterance for a missing opening. Missing recognition and mismatches remain failures. The result reports each trial and the total preserved count without copying utterance text. Exit 0 means all recorded prefixes match; exit 2 means at least one does not. Neither establishes physical qualification: `physical_capture_verified` remains false. Inputs are bounded to 1 MiB for plan/mapping and 32 MiB for transcript, and output cannot overwrite prior evidence.

Actual capture, independent recording/review, ten-minute echo observation and required physical cutoff measurements remain mandatory. Synthetic scorer tests do not count toward those gates. Keep recordings, transcripts and fixtures private and out of Git.

The live-PC acceptance entrypoint additionally requires a private directory containing `plan.json`, `mapping.json`, `session.json`, `review.json` and `acoustics.json`. The latter follows ACOUSTIC_EVAL.md and names recordings relative to this directory. Use all its spoken-interruption trials; do not omit poor outcomes. `OpeningReview` binds the canonical mapping digest and exact acoustic-manifest byte hash, names an independent human reviewer, confirms pre-run label freezing, maps every case to a distinct acoustic trial, and gives a per-case utterance/overlap verification. The reviewer must check actual uttered words against independent synchronized user audio, assistant overlap and provider identity; retain supporting recordings and review notes, not merely check boxes. The automated check verifies declared bindings, WAV hashes/calibration and exact transcript prefixes; it cannot authenticate a reviewer or physical origin.

```powershell
$env:IAGO_PC_OPENING_FIXTURES = 'C:\private-fixtures\openings'
uv run --extra vision --extra robot iago-verify --suite live-pc --select physical_opening_words --output local-data/evidence/physical-openings
```

Missing required files produce blocked; malformed evidence or missing opening words fail. Recorded-event duplicates, reused trials, provider mismatches and unreviewed cases are rejected. The original cutoff percentile gate remains separate even though this check verifies its recording evidence, and the ten-minute echo observation is still required. No reviewed physical dataset is bundled.
