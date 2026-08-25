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
