# Retrieval similarity-floor calibration

**Status:** complete · **Date:** 2026-08-25 · **Result:** floor `0.40`, per-source cap `2`

This document records how the two retrieval constants were chosen. Both are
configuration (`RETRIEVAL_MIN_SIMILARITY`, `RETRIEVAL_MAX_PER_SOURCE`), and
both were set from measurement rather than taste.

## Why pre-registration

The similarity floor decides whether the system answers a question or says the
transcripts do not support it. Set it too high and real questions are refused;
too low and weak, off-topic chunks get cited as evidence — which is the failure
this product exists to avoid.

A threshold picked *after* looking at the scores is not a measurement. It is a
curve fit, and it would make every downstream evaluation case self-confirming:
the eval set would pass because the line was drawn where it had to be.

So the question set was written, labelled, and **committed before this
calibration ran**:

```
87538dd  eval: pre-register the retrieval calibration question set
```

That commit contains `backend/tests/eval/calibration_set.json` and nothing
else. The git history is the pre-registration. The file's own `_rules` bar
post-hoc edits; a genuinely wrong entry may only be corrected through an
explicit `revisions[]` entry, after which the calibration is re-run from
scratch and re-reported. **`revisions` is currently empty — no question was
changed after scoring.**

## The frozen set

25 questions against the 20-episode curated corpus (commit `be8ab89`),
embedded with `all-minilm` at 384 dimensions.

| Class | n | Meaning |
|---|---|---|
| `supported` | 16 | The answer demonstrably exists; each names the episode expected to supply it |
| `unsupported` / `near_miss` | 5 | Plausibly on-topic growth/product questions the corpus does **not** cover |
| `unsupported` / `off_domain` | 4 | Unrelated subjects (gardening, plumbing, history, baking) |

Near-miss questions carry the weight here. Off-domain questions alone would
make almost any threshold look excellent; the honest test is whether a
reasonable growth question that the corpus happens not to cover — Series B
liquidation preferences, App Store appeals, GDPR subprocessors, Kubernetes
autoscaling, software patent strategy — is correctly refused.

## Result: the populations separate

Scored on top-1 cosine similarity.

| Class | n | min | p50 | max |
|---|---|---|---|---|
| supported | 16 | **0.4123** | 0.6551 | 0.7426 |
| near_miss | 5 | 0.2689 | 0.3663 | **0.3811** |
| off_domain | 4 | 0.2325 | 0.2520 | 0.3013 |

```
lowest supported     0.4123
highest unsupported  0.3811
separation margin   +0.0312   (clean separation, no overlap)
```

Confusion matrix across candidate thresholds:

| threshold | TP | FN | FP | TN | accuracy |
|---|---|---|---|---|---|
| 0.30 | 16 | 0 | 5 | 4 | 80% |
| 0.35 | 16 | 0 | 3 | 6 | 88% |
| 0.38 | 16 | 0 | 1 | 8 | 96% |
| **0.39 – 0.41** | **16** | **0** | **0** | **9** | **100%** |
| 0.45 | 15 | 1 | 0 | 9 | 96% |
| 0.50 | 13 | 3 | 0 | 9 | 88% |
| 0.60 | 12 | 4 | 0 | 9 | 84% |

**Chosen: `0.40`.** It sits inside the separating gap `[0.3811, 0.4123]`, is
within 0.0033 of the exact midpoint (0.3967), and scores 100% on the frozen
set. It is stated to two decimals deliberately: a 25-question set does not
justify four significant figures of apparent precision.

## Honest limitations

- **The margin is thin.** 0.031 of cosine similarity, on n=25. The separation
  is real but it is not robustly validated, and a modest change to the corpus
  or the question mix could close it. This is the weakest number in Phase 3.
- **Near-miss questions cluster just below the line** (max 0.3811 vs floor
  0.40). The margin against *off-domain* questions is comfortable; the margin
  against plausible-but-uncovered questions is not.
- **The set is small and single-author.** A production deployment would want
  a larger set, ideally labelled by someone who did not write the retriever.
- Scores are specific to `all-minilm` at 384 dims and to this 20-episode
  corpus. **Changing either invalidates the floor and requires re-calibration**
  — which is one more reason the embedding model is pinned per chunk and
  enforced at query time.

## Second finding: the per-source cap

`RETRIEVAL_MAX_PER_SOURCE` limits how many chunks one episode may contribute.
Measured over the 16 supported questions at floor 0.40:

| Setting | total chunks | avg chunks/answer | distinct sources | avg sources/answer | single-source answers |
|---|---|---|---|---|---|
| no cap | 44 | 2.75 | 24 | 1.50 | 7 / 25 |
| **cap = 2** | 43 | 2.69 | **30** | **1.88** | **1 / 25** |

The cap costs **one chunk across sixteen questions** and gains **six distinct
sources**. Only S01 ("How does Duolingo use streaks to improve retention?")
drops from 3 chunks to 2, because all 18 nearest chunks come from that single
episode — which is the correct behaviour, not a defect.

Critically, the floor is applied *before* the cap, so a substituted
third chunk must still clear 0.40 to be included. The cap can therefore only
add genuinely relevant corroboration; it can never pad an answer with noise.
That is why it is safe to enable by default.

## Attribution accuracy (reported, not tuned)

Over the 16 supported questions, uncapped:

- expected episode in **top-3: 14/16**
- expected episode at **top-1: 11/16**

Two questions did not surface their expected episode at all:

- **S11** "Why do product managers from big companies struggle at early-stage
  startups?" → returned `emily-kramer`, expected `casey-winters`
- **S16** "How should a leader handle layoffs humanely?" → returned
  `geoff-charles`, expected `merci-grace`

Both were still correctly classified as *supported* (they cleared the floor),
so the floor calibration is unaffected. But they are genuine retrieval misses
and are reported as such. **They were not fixed by editing the question set** —
that is exactly what the pre-registration rules forbid. They stand as the
measured top-1 accuracy of `all-minilm` on this corpus, and as the strongest
available argument for the documented `nomic-embed-text` upgrade path.

## Reproducing

```bash
cd backend
python -m app.ingest                        # 20 episodes, ~53s
python tests/eval/run_calibration.py        # no cap
python tests/eval/run_calibration.py --cap 2
```

Raw per-question output is written to
`backend/tests/eval/calibration_results_cap-*.json`.
