# Ship 30 essay generation — decisions and measurements

Phase 6. How a verified answer becomes a ~1,250-word essay, and why each part
works the way it does. Companion to `agent-framework-comparison.md`, which
covers why the agent framework is Pi at all.

---

## 1. How the skill reaches the model

**Not through `pi --skill`.** That flag exists and looks like the obvious
answer, so the reason it is unusable here is worth recording precisely.

Pi implements the [Agent Skills standard](https://agentskills.io/specification)
with **progressive disclosure**. From its own `docs/skills.md`, §"How Skills
Work":

> 1. At startup, pi scans skill locations and extracts names and descriptions
> 2. The system prompt includes available skills in XML format
> 3. **When a task matches, the agent uses `read` to load the full SKILL.md**
> 4. The agent follows the instructions

Step 3 is the problem. The body of `SKILL.md` reaches the model only when the
model calls the **`read` tool** — and this application runs Pi with
`--no-tools`, a Phase 4 decision made because read/write/edit/bash in a web
backend is a live liability. Under our configuration `--skill` would inject a
name and a description the model has no way to expand.

The documented escape hatch, `/skill:name`, is an interactive slash command;
we run `-p` (non-interactive). Re-enabling `read` to make `--skill` work would
trade the tool-surface guarantee for a formatting convenience.

**What we do instead.** Pi's `--system-prompt` and `--append-system-prompt`
both accept a *file path* and read its contents
(`dist/core/resource-loader.js:15-29` — `existsSync(input)` →
`readFileSync(input)`). So the skill is delivered as a prompt file:

```
--system-prompt         <workdir>/system-<uuid>.txt   backend/app/prompts/ship30_rules.md
--append-system-prompt  <workdir>/append-<uuid>.txt   .claude/skills/05-ship30-writing/SKILL.md
--no-tools --no-skills --no-prompt-templates
```

### The split is the point

| Channel | Content | Owner | Can an edit relax grounding? |
|---|---|---|---|
| `--system-prompt` | evidence-only, `[E#]` tags, verbatim quotes, no invented speakers/statistics, Markdown-only | **the code** (`app/prompts/`) | no — it ships with the code and is verified after generation |
| `--append-system-prompt` | voice, structure, style, the Hook→takeaway progression | **the skill** (`.claude/skills/`) | no — the rules it would have to remove are not in this file |

`test_rules_are_application_owned_not_skill_owned` asserts the separation
mechanically: each non-negotiable must be present in the rules file and
**absent** from the skill file.

### Two hazards this closes

**A missing file becomes the prompt.** `resolvePromptInput` returns the
argument *as literal text* when the path does not exist. A typo'd path would
silently become the instructions, and nothing downstream could tell. The
application therefore reads the skill itself before spawning anything
(`ProviderMisconfigured` if absent), and `PiRuntime` writes the files it is
about to reference, so the path always exists.

**Ambient skills and prompts.** Pi discovers skills from `~/.pi/agent/skills/`
and `~/.agents/skills/`, and an append-prompt from `~/.pi/agent/APPEND_SYSTEM.md`
— all **global**, so the controlled-working-directory fix from Phase 4 does not
cover them. `--no-skills` is now passed on every call, and passing
`--system-prompt` explicitly overrides `SYSTEM.md` discovery.

---

## 2. Packaging: `.claude/` is not in the image

`.dockerignore` excludes `.claude/`, and the Dockerfile copies only
`backend/app`, `backend/migrations` and `backend/alembic.ini`. A runtime read
of `.claude/skills/…` therefore **works on the host dev loop and fails under
`docker compose up`** — the exact deployment an evaluator runs, and the worst
shape of bug available: invisible until the graded environment.

Closed with two lines, both regression-tested (`TestSkillIsShipped`):

- `.dockerignore` re-includes `!.claude/skills/05-ship30-writing/`
- the Dockerfile copies it to `app/skills/05-ship30-writing/SKILL.md`

There remains exactly **one authored copy** in git, so the two cannot drift.
`ship30._SKILL_CANDIDATES` looks in `app/skills/` first (container) and
`.claude/skills/` second (host).

---

## 3. Evidence: carried over, then topped up

An essay is written from the evidence the reader was already looking at.

1. **Pin.** `messages.sources` now carries `chunk_id` / `transcript_id`, so
   `retrieval.evidence_by_chunk_ids()` re-reads those exact chunks **by primary
   key, in stored order**. Not a search — a search could return different
   material under the same labels. These keep positions `[E1]…[En]`, which is
   what makes a citation mean the same thing in the essay as in the answer.
2. **Top up.** The originating question is re-run through the ordinary
   `retrieve()` at `ESSAY_RETRIEVAL_K` (default 6); anything already pinned is
   dropped, the rest append as `[E4]…`.

Retrieval itself is unchanged: same exact search, same total tie-break, same
floor (0.40), same per-source cap (2). Only `k` differs, and `k` was already a
parameter. **The pre-registered calibration remains valid** — it measured where
the *floor* separates supported from unsupported questions, and the floor has
not moved.

Why top up at all: 1,250 words from 2–3 chunks is thin, and a model that runs
out of material is a model under pressure to invent some. See §5.

**If a chunk is gone, the request fails** (`evidence_unavailable`, 409). This
is a real case, not a theoretical one: `python -m app.ingest --force`
CASCADE-deletes chunks and re-creates them with new UUIDs. Silently
re-retrieving would swap the evidence behind the reader's back.

---

## 4. Entry conditions

Enforced server-side, for every caller. A hidden button is not an access
control.

| Condition | Response |
|---|---|
| session or message unknown | `404 not_found` |
| message belongs to another session | `404` — reported absent, not forbidden (a 403 would confirm it exists elsewhere) |
| `role != "assistant"` | `422 validation_failed` |
| `sources == []` — an abstention | `422` — no evidence means the model was never invoked; writing anyway would mean writing from its memory |
| `grounding` FAIL **or NULL** | `409 conflict` |
| a cited chunk no longer exists | `409 evidence_unavailable` |

**No essay is written from an answer that failed verification.** Building 1,250
confident words on an answer already known to contain fabricated quotes would
launder a failure into a longer, more shareable artifact. A missing verdict is
refused on the same footing as a failed one — NULL is not a PASS.

---

## 5. Grounding, and one blind spot we did not close badly

`verify_answer` runs on every essay, unconditionally, against the same evidence
list it was built from. **`grounding.py` is unchanged** — same `MIN_QUOTE_WORDS
= 2`, same `QUOTE_RE` covering straight and curly quotes, same `normalize()`.
Phase 6 adds essay-length coverage, not new thresholds.

This matters because the local model has already failed this exact task.
`spike/results/bench.json`, Test C, `qwen3:4b-instruct` writing a Ship 30
essay: **6 fabricated quotes out of 28**, pinned by
`test_catches_the_real_phase1_fabrications`. **Retraction on the local path is
the expected outcome for essays, not an edge case** — which is why the
retracted state is a first-class screen rather than an error page.

### The blockquote blind spot

`QUOTE_RE` matches only `"…"` and `"…"`, so a fabricated pull-quote inside a
Markdown `>` block is never examined.

The obvious fix — extend extraction to blockquote lines — was **tested against
the real Phase 1 essay and rejected**. Its 4 blockquote lines are a *sources
list*:

```
> [E1] Brian Balfour — "Why ChatGPT will be the next big growth channel…" (2025-08-17, 00:0…)
```

Naive extraction flags all four as fabricated, breaking the pinned 28/6
assertion and producing false positives on honest work. **A detector that flags
honest work is as useless as one that misses lies.**

So Phase 6:

- instructs against blockquote quotation in `ship30_rules.md` (rule 4);
- **counts** blockquote lines and reports `blockquote_lines` in `done` and in
  the log line;
- leaves `extract_quotes` alone.

Closing this properly needs its own calibration against real essay output.
Recorded as a known gap, not silently carried.

---

## 6. The word target

**Measured and reported. Never enforced.**

Truncating an essay to hit 1,250 words would cut quotes and citation tags
mid-sentence and could turn verified prose into a fabrication. Regenerating
would double a ten-minute local run and hide the miss. So `word_count()` is one
function applied to exactly the bytes that get persisted, `within_target`
(1,000–1,500) is surfaced as a quality signal, and a miss is shown to the
reader as "off target".

Measured output lengths for this exact task:

| Model | Words | Output tokens | Wall clock |
|---|---:|---:|---:|
| `qwen3:4b-instruct` (Ollama) | 1,578 | 2,220 | **619.6 s** |
| `deepseek-v4-pro` | 1,024 | — | 75 s (a separate run returned **empty at 4,096 output tokens**) |

Two configuration consequences followed:

- **`deploy/pi-models.json` capped qwen3 at `maxTokens: 2048`** ≈ 1,500 words.
  Phase 1 measured 2,220 tokens for this task, so a verbose essay was
  **truncated in the container** while running fine on a host whose
  `models.json` has no such cap. Raised to **3072**; the prompt is ~2,000
  tokens against an 8,192 window, so it still fits with headroom.
- **`deepseek_max_tokens` and `deepseek_disable_thinking` are defined in
  `config.py` and used nowhere.** DeepSeek's empty Test C result is consistent
  with thinking consuming the whole output budget. Pi exposes `--thinking`;
  wiring it is recorded as follow-up rather than assumed to be needed.

---

## 7. Latency, and the provider UX contract

Requirement 1 of the locked contract — streaming is baseline UX for every model
request — is satisfied by **reusing the chat protocol unchanged**:
`meta → sources → delta* → grounding → done | error`. Against a measured 619 s:

- `sources` lands in ~1 s, so the citation cards are on screen almost at once;
- the first `delta` follows prefill (~31 s measured), so the pane is never
  blank for minutes;
- the pane shows a **live word count and elapsed clock**, which is what makes a
  ten-minute stream legible rather than ambiguous;
- the button warns before starting on a local session;
- the pane opens itself when generation begins, so an essay is never being
  written behind a collapsed panel.

**No generation timeout is added.** "Model timeout" is an open Phase 8 row; it
is stated here rather than half-implemented. A disconnect mid-generation
discards the work — a partial essay is not an essay, so nothing is persisted.

---

## 8. Storage

A dedicated `essays` table (migration `0004`), not `messages.kind`. An essay is
not a turn: it must not appear in the conversation transcript, must not enter
`retrieve_for_session`'s history — a 1,250-word turn would quietly steer the
next retrieval — and needs to be addressable on its own for the Artifact
Viewer. `format` is explicit so Phase 7 can add rendered HTML unambiguously.

Provenance covers the instructions as well as the model: `skill_name` and
`skill_sha256` pin the exact revision of `SKILL.md` that produced each essay,
which is what makes skill 03's "expose the selected skill" answerable rather
than assumed.

---

## 9. Rendering (and what Phase 7 still owns)

The Artifact Pane renders the essay as **escaped text** — a React text node in
a `<pre>`, the same escaping path the chat body already uses. There is no
`dangerouslySetInnerHTML`, no `iframe`, no Markdown→HTML step, and **no
Markdown library in `package.json`**.

So a reader sees the generated Markdown *source*. That is the honest
intermediate state: the essay is real, complete and verifiable now, and it
gains formatting when there is a stated policy for rendering untrusted markup
safely. Phase 7 owns sanitization, isolation and CSP. Building the frame first
and the security policy second is safe in that order; the reverse is not.

---

## 10. Measured: why local Ship 30 essays retract (2026-08-25)

Manual testing reported frequent hallucination on the local path. This section
records what was measured, what caused it, what was tried, and what was
concluded. **No threshold was changed and `grounding.py` was not touched** —
`MIN_QUOTE_WORDS` is still 2 and `QUOTE_RE` is byte-identical.

### 10.1 Method

Driven over the real HTTP path against the container (`POST /api/sessions` →
`POST /sessions/{id}/messages` → `POST /sessions/{id}/essays`), so every run
used the shipping Pi 0.84.3 and the `deploy/pi-models.json` `maxTokens: 3072`
cap. Two questions, both already characterised in this document:

- **duolingo** — "How does Duolingo use streaks to drive retention?" (episode-specific)
- **growthteam** — "What makes a growth team effective?" (broad, multi-episode)

n=3 per question per provider. **n=3 is an indicative engineering comparison,
not a claim of statistical significance.**

### 10.2 Baseline

| | attempts | essays produced | PASS | FAIL | fabricated / checked | rate | median words | median wall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Ollama `qwen3:4b-instruct` | 6 | 6 | **0** | **6** | **16 / 72** | **22.2%** | 1,165 | 207.0 s |
| DeepSeek `deepseek-v4-pro` | 6 | 2 | 2 | 0 | 0 / 0 | n/a | 1,186 | 117.1 s |

Two facts behind the DeepSeek row matter more than the verdict column:

- **3 of 6 DeepSeek runs never produced an essay at all** — the stream died
  with `internal_error` *after* a complete essay had been generated (6,831 /
  7,486 / 7,765 characters, all discarded). Cause in §10.5.
- **Both DeepSeek passes were vacuous: `quotes_found: 0`.** Those essays contain
  no verifiable quotation. DeepSeek passed by not quoting, not by quoting
  accurately. A fourth run had its *short answer* fail verification, so no
  essay was attempted.

Meanwhile **every Ollama short answer verified clean (6/6 PASS) while every
Ollama essay failed (6/6 FAIL)** — same model, same evidence, same verifier.
Whatever is happening is specific to essay length, not to the model's ability
to quote at all.

### 10.3 What is actually being fabricated

All 16 baseline fabrications were classified against the evidence, the prior
answer, and the evidence with inline speaker labels stripped:

| class | count |
|---|---:|
| wholly invented | 14 |
| altered from evidence | 2 |
| lifted from the prior answer | **0** |
| crosses a speaker label | **0** |

The invented spans are **plausible product microcopy placed inside quotation
marks**:

```
"You've just broken your 30-day streak"
"Today's lesson has been updated"
"This is how many days you've used Duolingo."
"you're doing great!"
"I will keep learning every day."
```

One essay wrote: *the first version used flavor copy that celebrated the user
— "you're doing great!" or something similar.* The model **signalled its own
approximation in the prose and still used quotation marks**. This is a quoting
discipline failure, not random hallucination.

### 10.4 Prompt and context analysis

Ground truth from Ollama's own tokenizer (`prompt_eval_count`), the numbers
that actually consume the 8,192-token window:

| component | chars | tokens |
|---|---:|---:|
| rules, `--system-prompt` | 3,704 | 866 |
| skill, `--append-system-prompt` | 694 | 161 |
| evidence block | 7,413 | 1,777 |
| prior answer (framing) | 995 | 226 |
| question | 49 | 20 |
| task tail | 817 | 96 |
| **total input** | | **~3,150** |

Plus Pi's measured 111-token harness, against ~1,700 output tokens for a
1,200-word essay: **roughly 5,000 of 8,192, about 62% utilisation.** Nothing is
truncated and nothing is evicted. **Context pressure is ruled out**, as is
evidence starvation (4 items carried + topped up, floor and cap unchanged).

What the numbers do show is **distance**: the verbatim-quote rule sits at
token 0 of a 3,150-token input, and the model then decodes ~1,700 tokens. By
the time it is writing body paragraphs the rule is 3,000–4,900 tokens behind
it. The answer path does not have this shape — `agent.stream_answer` passes no
`system_prompt` at all and inlines its rules into the user message directly
above the evidence, a few hundred tokens from the text being written.

### 10.5 A defect found while measuring: Pi's 64 KiB line limit

`pi_runtime.stream()` consumes Pi's JSON-lines with `async for raw in
proc.stdout`. `asyncio.StreamReader` caps a single line at **64 KiB** and
raises `LimitOverrunError` → `ValueError` past it. Pi's terminal events
(`turn_end`, `agent_end`) echo the whole conversation, **including thinking
content**. Measured in-container on a real essay prompt:

| event | max bytes |
|---|---:|
| `agent_end` | 55,027 |
| `turn_end` | 45,909 |
| `message_update/thinking_end` | 38,518 (8,656 thinking deltas) |

That run survived at 84% of the limit; three of six baseline runs did not. The
result is an unhandled `ValueError`, a terminal `internal_error` on the SSE
stream, and a complete essay discarded after two to four minutes of
generation. It is **pre-existing since Phase 4**, not a Phase 6 regression, and
it is why `deepseek_disable_thinking` being unwired (gap 13) has a cost.

**FIXED 2026-08-25** — see §11. It was deferred out of the investigation
commit because it is neither a prompt change nor a layout change, then fixed on
its own with its own regression tests.

### 10.6 Mitigation attempted, and its measured result

One evidence-preserving, prompt-level change was tried, targeting §10.3/§10.4
directly: **restate the quoting rule in the user prompt at the TASK line**,
adjacent to the instruction that triggers generation, while leaving
`ship30_rules.md` authoritative in `--system-prompt`. Cost about 90 tokens.

| | essays | PASS | FAIL | fabricated / checked | rate |
|---|---:|---:|---:|---:|---:|
| Ollama baseline | 6 | 0 | 6 | 16 / 72 | 22.2% |
| Ollama with restatement | 6 | **0** | **6** | 17 / 97 | 17.5% |

Per question: duolingo 20.9% → 13.6%, growthteam 24.1% → 23.7%.

**The change was reverted.** The per-quote rate moved in the right direction on
one question and not the other, the model attempted more quotations (72 → 97),
the absolute number of fabrications went *up* (16 → 17), and **not one of
twelve essays changed verdict**. At the product level — where the outcome is
"this essay is retracted in front of the reader" — it did nothing. Shipping a
prompt change that alters no outcome would be bloat that implies a fix.

DeepSeek in the same round: 4 essays completed (1 crash, 1 answer failure), 4
PASS — but 3 of the 4 still had `quotes_found: 0`. The one non-vacuous pass
checked 4 quotes and fabricated none.

### 10.7 Conclusion

**H1 (instruction distance) is real but is not the binding constraint. H2
(prior-answer bleed) and H3 (speaker labels) are ruled out by measurement:
zero occurrences of each. H4 — model capability at essay length — is the
conclusion.**

`qwen3:4b-instruct` is **not reliable for long-form Ship 30 generation under a
zero-tolerance verifier**. It quotes accurately in a 250-word answer and
invents illustrative copy in a 1,200-word essay, at roughly one fabricated
span per 75 words of output. With 12–22 quotations per essay and a per-quote
fabrication rate near 20%, the probability of a clean local essay is a few
percent, and a single fabricated span retracts the whole artifact. This is the
honest engineering finding; it is not something a prompt fixes.

That is a **correct system behaving correctly**: the verifier catches the
fabrications, the reader is shown a retraction, and no fabricated quote is ever
presented as sourced. Phase 1 predicted exactly this (6 of 28 quotes fabricated
on this same task) and it is why retraction is a first-class screen.

### 10.8 Recommendation

- **Keep Ollama as the mandated local path and keep it exactly as it behaves.**
  A retracted local essay is a working demonstration of the trust property, and
  it should be shown rather than hidden.
- **Demonstrate a successful Ship 30 essay on DeepSeek**, stating plainly that
  its passes frequently contain no quotations at all — a real difference in
  behaviour, not a quality ranking.
- **Do not raise thresholds, relax `QUOTE_RE`, or add a retry loop** to make
  the local path look better. Each would trade the product's trust property for
  a demo.
- Fix the 64 KiB stream limit (§10.5) before the DeepSeek essay path can be
  called reliable; today it discards roughly a third of completed essays.

---

## 11. The transport fix, and what it corrected in §10

The 64 KiB defect from §10.5 was fixed after the investigation, as its own
change. It turned out to have **biased the §10.2 DeepSeek measurements**, so
this section corrects them.

### 11.1 The fix

`pi_runtime.stream()` now passes an explicit `limit` to
`create_subprocess_exec` (`_STDOUT_LINE_LIMIT`, 16 MiB — about 300× the largest
event ever measured, 55,027 bytes) and reads with an explicit `readline()` loop
instead of `async for`, so an event past even that ceiling is **logged, counted
and skipped** rather than propagating out of the generator. asyncio drops the
offending line from its own buffer, so events after it still arrive.

Unchanged: the event protocol, `classify_event`, the error taxonomy, the
streaming contract, the scratch-file cleanup, `grounding.py`, prompts and
retrieval. The turn's log line gains `oversized_events`. A turn where
*everything* was unreadable now says so, instead of reporting "no output" and
sending an operator to look for a dead provider.

### 11.2 Verified on the path that used to fail

The same DeepSeek matrix from §10.2, re-run in the container:

| | attempts | essays produced | stream crashes | PASS | fabricated / checked |
|---|---:|---:|---:|---:|---:|
| before the fix | 6 | 2 | **3** | 2 | 0 / **0** |
| after the fix | 6 | **6** | **0** | **6** | **0 / 82** |

### 11.3 The correction

§10.2 concluded that *"DeepSeek passed by not quoting"*, on the evidence that
both of its surviving essays had `quotes_found: 0`. **That conclusion was an
artefact of the defect.** Event size scales with generation length and thinking
content, so the runs that crashed were systematically the *richer* ones — the
bug was silently filtering the sample down to the shortest, least quotational
essays.

With the transport fixed, DeepSeek checks **82 quotations across 6 essays and
fabricates none** (15, 35, 14, 13, 0, 5 per essay). One essay still used no
quotations; the other five did, and verified clean. DeepSeek is genuinely
reliable on this task, not vacuously reliable.

**What does not change:** the Ollama findings in §10.2–§10.7. Ollama produced
6 of 6 essays with zero crashes both before and after — its events never
approached the limit, because `qwen3:4b-instruct` emits no thinking content.
Its 0-of-12 pass rate, the 22.2% fabrication rate, the classification of all 16
spans, and the reverted mitigation all stand exactly as measured. **The H4
conclusion is unaffected.**

### 11.4 Recommendation, updated

§10.8 stands, with one strengthening: the DeepSeek demo path is now known to
produce **quote-rich, verifiably clean** essays rather than quote-free ones, so
"demonstrate a successful Ship 30 essay on DeepSeek" no longer needs the
caveat that its passes may contain no quotations.
