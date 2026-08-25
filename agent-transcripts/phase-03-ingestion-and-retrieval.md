# Phase 3 — Ingestion and deterministic retrieval

**Commits:** `87538dd eval: pre-register the retrieval calibration question set` ·
`147378d feat(phase-3): transcript ingestion and deterministic retrieval`
**Artifacts:** [`docs/retrieval-calibration.md`](../docs/retrieval-calibration.md)

---

## Correction 1 — an episode disappeared, and nothing said so

### What happened

Ingestion reported success. The corpus looked fine. Then a row count against the manifest showed
**19 episodes indexed out of 20**.

`casey-winters` had parsed to zero turns. The parser found no speaker-delimited content it
recognised, produced an empty list, and the pipeline — encountering nothing to insert — inserted
nothing and moved on. No exception. No warning. A clean `failed=0`.

### Why this is the worst class of bug in this product

Nothing about the running system looks wrong. Retrieval works. Answers are grounded, cited, and
verified against evidence that genuinely exists. The verification layer cannot catch it, because
every quote it checks is real.

The failure is invisible: the assistant simply never knows anything Casey Winters said, and
answers questions about his material from whatever else is closest — confidently, with citations,
and wrongly. **A quietly incomplete corpus produces confident wrong answers, and every trust
mechanism in the system passes it.**

### The correction

Make it unrepresentable rather than detected:

```python
CheckConstraint("turn_count > 0", name="ck_transcripts_turn_count")
```

A transcript row with zero turns cannot exist in the database. The pipeline now fails loudly,
names the file, and writes nothing — asserted by
`test_zero_turn_transcript_fails_loudly_and_writes_nothing`.

The parser was fixed too, but the constraint is the part that matters: the same class of failure,
from any future cause, now stops the ingest instead of shrinking the corpus.

---

## Correction 2 — pre-registering the thresholds, to stop myself tuning them

### The problem with tuning a retrieval threshold

`RETRIEVAL_MIN_SIMILARITY` decides when the system says "the transcripts do not support this"
instead of answering. Set it too low and the assistant answers everything, badly. Too high and it
abstains on questions it could have answered.

The tempting workflow — try a value, look at the results, adjust — is how you fit a threshold to
the questions you happened to test and call it calibration.

### What was done instead

**The question set was committed before the run.** `87538dd` lands the frozen evaluation set as its
own commit, with no results in it. Only then was the calibration executed, in `147378d`.

The separation is visible in the git history, which is the point: the questions cannot have been
chosen to flatter the answer.

### The result, including the uncomfortable parts

| Measure | Value |
|---|---|
| Separation between supported and unsupported questions | **+0.031** on n=25 |
| Chosen threshold | `RETRIEVAL_MIN_SIMILARITY = 0.40` |
| Unsupported questions correctly abstaining | 9/9 |
| Correct episode retrieved at **top-1** | **11/16** |

Two of those numbers are weak, and both are recorded as gaps rather than smoothed over:

- **A +0.031 margin is thin.** Supported questions scored ≥ 0.4123 and unsupported ≤ 0.3811. It
  works on this corpus and it would not take much drift to stop working. Gap #3.
- **11/16 at top-1 means two supported questions miss their expected episode entirely.** Gap #4,
  reported and not tuned away — because tuning it against these 16 questions is precisely the thing
  pre-registration was set up to prevent.

Changing either threshold without re-running the calibration invalidates the eval set, and that is
written into [`CLAUDE.md`](../CLAUDE.md) so a future agent cannot casually "improve" it.

---

## Design decision: retrieval is a query, not a tool

The agent never decides whether to search, what to search for, or when to stop. Retrieval is
`ORDER BY embedding <=> query` with a **total** tie-break on
`(distance, transcript_id, chunk_index)`.

Exact search, no ANN index — at 1,395 chunks there is no latency argument for approximate search,
so accepting less than 100% recall would buy nothing. Determinism here is a correctness property:
the same question returns the same evidence, which is what makes a citation reproducible and a
regression detectable.

Handing this to the model as a tool would have made evidence selection non-deterministic and moved
a decision that must be auditable into a place where it cannot be audited.
