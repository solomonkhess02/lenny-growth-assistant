# Phase 6 — Ship 30 essays

**Commits:** `debea40 feat(phase-6): Ship 30 essay generation from verified answers` ·
`6b1dac7 docs(ship30): measure the local hallucination problem and conclude it` ·
`16431f5 fix(pi): stop asyncio's 64 KiB line limit discarding finished generations`
**Artifacts:** [`docs/ship30-essays.md`](../docs/ship30-essays.md)

The most instructive phase in the build, because the headline correction is a fix that **worked and
was reverted anyway**.

---

## Correction 1 — the prompt mitigation that was measured and thrown away

### The problem

Ship 30 essays generated on `qwen3:4b-instruct` fabricate quotations. Measured at n=3 per
question: **0 of 12 essays passed** verification, at roughly **20% per-quote fabrication** across
12–22 quotations per essay. The fabrications are invented product microcopy placed inside quotation
marks — the most convincing possible form.

Two things the measurement ruled out, both of which had been plausible:

- **0 came from the prior answer.** The essay is not laundering an earlier mistake.
- **0 crossed a speaker label.** It is not misattributing a real quote to the wrong person.

They are simply invented.

### The fix that worked

A prompt-level mitigation — stronger, more specific instruction about quoting only from the
supplied evidence. It was implemented and measured properly, at the same n:

| | Per-quote fabrication rate |
|---|---|
| Baseline | **22.2%** |
| With mitigation | **17.5%** |

A 21% relative reduction. Real, reproducible, in the right direction.

### Why it was reverted

**It changed zero verdicts.** Every essay that failed before still failed after.

An essay containing 15 quotations fails if *any one* is fabricated. Dropping the per-quote rate
from 22.2% to 17.5% moves the probability that all 15 are clean from about 2.5% to about 5.9% —
both of which round to "this does not work."

So the mitigation improved the **metric** without improving the **outcome**. Keeping it would have
meant carrying extra prompt complexity, and a documented "improvement", for something that never
once turned a failing essay into a usable one.

Reverted, with both numbers recorded in [`docs/ship30-essays.md §10`](../docs/ship30-essays.md).

### The general lesson

This is the clearest case in the build of the agent optimising an observable instead of the thing
the observable stands for. The measurement was correct, the direction was right, and the change was
still worthless — and only checking the *verdicts* rather than the *rate* revealed it.

The conclusion recorded in `CLAUDE.md` is deliberately blunt, aimed at a future agent: **do not
raise thresholds, relax `QUOTE_RE`, or add a retry loop to improve this.** Every one of those makes
the number better and the product worse. It is a model limit, and the retraction is the system
working.

---

## Correction 2 — finished essays were being thrown away

### The symptom

**3 of 6** DeepSeek essay runs died after two to four minutes. Each had *completed* — a full
6.8–7.8 KB essay existed — and each surfaced to the client as a bare `internal_error`.

Intermittent, expensive, and it looked like a network or provider problem.

### The cause

`pi_runtime` iterated `proc.stdout`. `asyncio.StreamReader` caps a single line at **64 KiB** and
raises `LimitOverrunError` → `ValueError` beyond it.

Pi's `turn_end`/`agent_end` events echo the entire conversation **including thinking content**, so
they scale with the generation. Measured in-container on a real essay prompt: `agent_end` at
**55,027 bytes**, from 8,656 thinking deltas.

So the failure was structural, not random: the longer and better the essay, the more certain it
was to be destroyed at the very last event. It had been latent since Phase 4 and was not a Phase 6
regression — it only became visible once generations got long enough.

### The correction

```python
limit=_STDOUT_LINE_LIMIT      # 16 MiB, ~300x the largest event measured
```

plus an explicit `readline()` loop that **logs, counts (`oversized_events`) and skips** anything
past even that ceiling, rather than letting the exception escape and discard a finished essay.

Six regression tests, each of which fails against the pre-fix module with the production exception.
Verified on the path that failed: the same DeepSeek matrix went **2 essays / 3 crashes → 6 essays /
0 crashes**.

---

## Correction 3 — the obvious blockquote fix, tested and rejected

Quote verification examines quotation marks. A fabricated pull-quote inside a Markdown `>` block is
never checked — a real hole.

The obvious fix is to extract blockquote lines and verify them too. It was implemented and
**rejected on measurement**: the real Phase 1 essay's blockquotes are a *sources list*, so naive
extraction flagged four honest lines as fabrications and broke a pinned assertion.

Closing it properly needs its own calibration set. So instead: the essay prompt instructs against
blockquote quotation, and a `blockquote_lines` count is reported on every essay — **0 in all live
runs**. Recorded as open gap #10 rather than shipped broken.

---

## Design decisions locked here

- **An essay is written from a verified answer, or not at all.** Abstentions refused with 422,
  failed and NULL verdicts with 409. Twelve hundred confident words built on a known fabrication
  would launder a failure into a *more* shareable artifact.
- **Evidence is rehydrated by `chunk_id`, never re-searched.** A missing chunk fails with
  `evidence_unavailable` rather than silently swapping different material under the same `[E#]`
  labels.
- **The word target is measured and reported, never enforced.** Truncating to 1,250 words would
  sever a quotation or a citation tag mid-sentence — turning verified prose into a fabrication at
  the final step.
