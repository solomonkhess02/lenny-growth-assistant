# PRD — The Lenny Growth Assistant

**Status:** built and verified through Phase 8.
**Evidence of record:** [docs/verification-matrix.md](docs/verification-matrix.md) — one row per
requirement, each backed by an executed test or a performed manual step, including what is *not*
met.

---

## Part 1 — Forward Deployment Brief

The discovery work, written before implementation and revised only where measurement contradicted
it. Every number below was measured on this build; none is an estimate.

### 1.1 User and problem

**Primary user: a product or growth practitioner** — a PM, growth lead or founder — at a company
where the team has decided Lenny's Podcast is a credible source of operating knowledge.

**The job they are trying to complete:** *"Someone I trust has probably already solved the problem
in front of me. Find what they actually said about it, and let me use it."*

Today they do one of three things, and all three fail differently:

| What they do now | Why it fails |
|---|---|
| Search their memory of episodes | Recall is vague and unattributable. "I think Duolingo did something with streaks" is not something you can put in a strategy doc |
| Scrub through a 90-minute episode | Finds the quote, costs an hour, and only works if you already know which episode |
| Ask a general chatbot | Returns plausible, fluent, *unverifiable* claims. This is worse than useless in a document that others will act on, because it is confidently wrong in the same voice it is confidently right |

**The pain the assistant removes** is not "reading transcripts is slow." It is that **advice you
cannot attribute is advice you cannot use.** A growth practitioner recommending a change to their
company's onboarding needs to say *who* said it, *where*, and be able to hand a colleague a link
that proves it.

So the product's job is narrower and harder than answering questions: it is producing answers a
professional can **stake their credibility on**, and refusing when it cannot.

### 1.2 Success metric

**Primary — the one this build is organised around:**

> **No answer reaches a user carrying an unverified quotation or an unresolvable citation.**
> Target: 100%. Measured: **100%.**

This is measurable rather than aspirational because verification is structural, not sampled:
`verify_answer` runs on *all* generated output, unconditionally, and a failure retracts the answer
in the interface. It is the metric that maps directly to the user's actual job — an answer they can
stake credibility on — and it is deliberately a *safety* metric rather than a quality one, because
a fluent wrong answer is the failure mode that destroys the product's reason to exist.

Note what this metric does **not** claim: not that every answer is good, nor that the local model
writes well. It claims that nothing unverified is presented as verified. On the local model, that
means essays are generated and then visibly retracted — the metric holding, at the cost of the
feature.

**Supporting metrics, all measured:**

| Metric | Target | Measured |
|---|---|---|
| Unsupported questions correctly abstain | 100% | **9/9** |
| Attribution contract populated on every chunk | 100% | **1,395/1,395**, 0 violations |
| Retrieval separation (supported vs unsupported) | > 0 | **+0.031** on n=25 — thin, and reported as thin |
| Correct episode retrieved at top-1 | — | **11/16**. Reported, not tuned away |
| Time to a grounded local answer | usable | **28.9 s** in-browser |

### 1.3 Assumptions

Made because the brief was incomplete. Each is stated so it can be challenged.

1. **A single trusted team, no authentication.** The brief says "internal assistant" and specifies
   no identity model. Sessions are anonymous; `user_metadata` exists as an opaque JSONB bag so
   identity can be added later without a migration. **If wrong:** per-user auth is required before
   any multi-tenant deployment.
2. **Twenty episodes is a sufficient corpus.** The full archive is large and unlicensed. Twenty
   pinned episodes exercise every retrieval and attribution property honestly. **If wrong:** the
   manifest is the only file that changes.
3. **Depth beats breadth.** One knowledge base answered well is worth more than several answered
   shallowly. **If wrong:** the ingestion pipeline is source-agnostic below the parser.
4. **The evaluator has consumer hardware, not a datacentre.** Everything is sized to a 4 GB GPU.
   This is why context is locked at 8,192 tokens and why the Claude Agent SDK was unusable.
5. **Recency is not a requirement.** No scheduled refresh. Ingestion is idempotent and manual.
   **If wrong:** it is a scheduler away, not a redesign.
6. **A wrong answer is more expensive than no answer.** The load-bearing assumption. Everything
   about the trust machinery follows from it, and if a client disagreed, this would be the first
   thing to revisit.

### 1.4 Scope choices

**In scope — built:**

- Grounded Q&A over the transcripts, with timestamp-level citations that deep-link to the exact
  moment in the source video
- Mechanical verification of every generated answer, and retraction when it fails
- Structural abstention when the corpus does not support the question
- Ship 30 for 30 essay generation from a *verified* answer, as an encoded skill
- An in-app Artifact Viewer with sanitization **and** sandbox isolation
- Per-session provider selection across a local and a cloud model
- One-command startup, structured errors, health endpoints, structured logs
- Failure-mode hardening: timeouts, deterministic teardown, correct error taxonomy under stream

**Deliberately excluded, with reasons:**

| Excluded | Why |
|---|---|
| Authentication / multi-tenancy | Assumption 1. Adding it badly is worse than not adding it |
| Automatic fallback between providers | A silent substitution makes the provenance stamped on an answer false. The absence is asserted by test |
| Persisting partial generations | A truncated essay is not an essay. Resuming would mean storing unverified text as if it were an artifact |
| Streaming-time verification | Verification cannot precede the text it verifies. Pretending otherwise would mean verifying a fragment |
| ANN vector index | 1,395 chunks. Exact search gives 100% recall with no accuracy/latency trade-off to defend |
| Blockquote quote-checking | The obvious implementation was tested and **rejected**: it flags honest sources lists. Documented as an open gap rather than shipped broken |
| A frontend test framework | The browser driver lives outside the repo. Real-browser verification is recorded in the matrix instead |
| Mobile-first responsive design | Desktop-first. The artifact viewer is a side-by-side reading surface; below 1100px it is hidden rather than degraded. See [design.md](design.md) |

### 1.5 Risks and trade-offs

The assignment names six. Each has a measured answer here, not a mitigation plan.

**Hallucination — the central risk, and it materialised.**
Measured: **0 of 12** local Ship 30 essays passed verification at n=3, at ~20% per-quote
fabrication — invented product microcopy inside quotation marks. Short answers on the same model,
evidence and verifier pass. It is long-form that breaks down.
*Response:* the system catches it every time and retracts. A prompt mitigation was measured
(22.2% → 17.5%) and **reverted**, because it moved the rate without changing a single verdict —
which would have been tuning the appearance of the problem. This is reported as a model limit, and
the retraction is the system working. [docs/ship30-essays.md §10](docs/ship30-essays.md)

**Latency.** A local grounded answer takes ~29 s; a local essay 254.7 s. Adopting Pi cost
measurable latency (Node process startup per request) and was accepted knowingly for a mandated
requirement. *Response:* stream tokens as they arrive, show an elapsed clock on essays — a
four-minute generation without one is indistinguishable from a hang — and bound both with idle and
total timeouts so a hang is never silent.

**Cost.** Local inference is free; cloud is metered. An abandoned generation used to keep running
unobserved, burning tokens after the reader had gone. *Response:* deterministic teardown — the
provider process is killed on disconnect, verified against a real process rather than assumed.

**Local-model quality.** Directly measured, above. The honest conclusion is that a 4B model is
sufficient for grounded short answers and **not** sufficient for 1,250-word grounded essays. The
product reports this rather than concealing it.

**Data leakage.** Transcripts are third-party and unlicensed for redistribution: pinned by hash and
fetched at ingest time, never committed. Credentials never enter the image, the logs, or an HTTP
response; the subprocess environment is stripped of keys a provider must not see. Session isolation
is structural — no unscoped read path exists to call by mistake.

**Unsafe artifact rendering.** Generated HTML is untrusted. *Response:* two independent gates —
server-side sanitization and a `sandbox=""` iframe with no scripts and no same-origin. Verified
in-browser against a real payload: zero scripts executed, zero external requests.
[docs/artifact-isolation.md](docs/artifact-isolation.md)

**The trade-off underneath all of them:** this product chooses *provable* over *impressive*. It
abstains, it retracts, and it refuses to write an essay from an unverified answer. A demo that
always produces confident prose would look better and be worth less.

---

## Part 2 — Product requirements

### 2.1 User flows

**Flow A — Ask a grounded question**
1. User creates a session and picks a provider (immutable for that session).
2. User asks a question.
3. Retrieval runs first. **If nothing clears the threshold, the model is never invoked** and the
   assistant states that the transcripts do not support the question.
4. Citations render **before** any text — they are evidence retrieved, not claims made.
5. The answer streams token by token.
6. Verification runs; the verdict renders below the answer.
7. On failure the answer is struck through under a banner naming the fabricated quotes.

**Flow B — Follow-up**
Retrieval resolves the follow-up against prior turns in the session. Context is session-scoped;
another session cannot see it.

**Flow C — Write a Ship 30 essay**
1. Offered only on an answer that **passed** verification.
2. Evidence is rehydrated by `chunk_id` — never re-searched, so `[E#]` labels cannot silently point
   at different material.
3. The essay streams into the Artifact Viewer with an elapsed clock.
4. Word count is measured and reported against ~1,250, **never enforced by truncation**.
5. Verification runs. A passing essay can be viewed Formatted; a failed one is retracted and
   confined to escaped source — the Formatted toggle is *disabled*, not merely unselected.

**Flow D — Switch provider**
Create a new session. There is no in-place switch, and no PATCH endpoint exists.

**Flow E — Reload**
Sources and verdicts are persisted, so a replayed conversation shows the same citations,
retractions and abstentions as the live one.

### 2.2 Acceptance criteria

Full table with evidence: [docs/verification-matrix.md](docs/verification-matrix.md). The
load-bearing ones:

| # | Criterion | Status |
|---|---|---|
| A1 | Unsupported question → explicit abstention, never a guess | ✅ 9/9 |
| A2 | Every generated answer is verified before presentation | ✅ structural |
| A3 | A failed verdict retracts the answer visibly | ✅ verified in-browser |
| A4 | Citations deep-link to the exact moment | ✅ followed to the video, `currentTime === 418` |
| A5 | Sessions do not leak context | ✅ test-enforced |
| A6 | A dead provider is surfaced, never substituted | ✅ test asserts the other provider is never named |
| A7 | Essays are written only from verified answers | ✅ 422 / 409 server-side |
| A8 | Generated HTML cannot execute | ✅ sanitize + `sandbox=""`, verified with a live payload |
| A9 | Partial output after a failure never reads as verified | ✅ verified in a real browser (M25) |
| A10 | One-command startup | ✅ `docker compose up` |
| A11 | Ship 30 essays verify clean on the local model | ❌ **0/12** — measured model limit, reported |

A11 is left visibly failed. It is the one place where the product's ambition exceeds what a 4B
model delivers, and the matrix says so rather than quietly restating the target.

### 2.3 Implementation plan — as executed

Each phase landed as a single linear commit on `main` with the matrix updated in the same commit.

| Phase | Delivered | Notable outcome |
|---|---|---|
| 1 | Provider/local-model spike | Predicted the local essay-quality ceiling before building on it |
| 2A/2B | Transport locked; FastAPI + Postgres skeleton | `127.0.0.1` over `localhost`: 2032 ms → 0.53 ms |
| 3 | Ingestion + deterministic retrieval | Thresholds set by **pre-registered** calibration |
| 4 / 4.6 | Pi agent layer; Pi in the image | Claude Agent SDK rejected on measurement, not preference |
| 5 | Chat UI, citations, retraction | Provider immutability and no-substitution locked |
| 6 | Ship 30 essays | Local hallucination measured at n=3 and concluded, not tuned |
| 7 | Artifact isolation | Sanitize **and** sandbox; CSP inheritance trap found in-browser |
| 8 | Failure-mode hardening | Timeouts, deterministic teardown, taxonomy under stream |
| 9 | Submission documentation | This document, README, architecture, design, transcripts |

### 2.4 What I would do next

Ordered by value, not by ease:

1. **Fix local essay quality properly** — the only ❌ in the matrix. Not by relaxing verification:
   by generating section-by-section against per-section evidence, so the model is never asked to
   hold 1,250 words of grounding in an 8,192-token context at once.
2. **Close blockquote verification**, with its own calibration set rather than the naive extractor
   that was tested and rejected.
3. **Pi in `--mode rpc`** to amortise the per-request Node startup that costs measurable latency.
4. **Widen the corpus** beyond 20 episodes and re-run attribution — 11/16 at top-1 is the weakest
   measured number in the system.
5. **Authentication**, if this ever serves more than one trusted team.
