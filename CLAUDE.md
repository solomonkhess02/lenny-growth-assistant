# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

"The Lenny Growth Assistant" — a full-stack conversational web app that ingests Lenny's Podcast
transcripts, answers product/growth questions grounded in them with source attribution, turns
those answers into Ship 30 for 30-style essays, and renders generated Markdown/HTML in an in-app
Artifact Viewer beside the chat. Take-home assignment, deadline **26 August 2026 EOD (IST)**; the
evaluator must be able to clone and run it from the documented steps alone.

## Current status — read before planning any work

**Phases 1–6 are complete and verified.** Backend, transcript ingestion, deterministic
retrieval, grounding verification, the Pi agent layer, the Docker agent path, the Phase 5 chat
UI and Phase 6 Ship 30 essays all ship (326 passed, 0 skipped on the host; 324 passed, 2 skipped
in the runtime image, as of 2026-08-25).

**Do not redo a completed phase unless a regression is demonstrated** — reproduce it first,
then fix it.

**Phase 7 is next: artifact isolation** — the Artifact Pane currently renders essays as escaped
text and must not render markup before that policy exists. Then failure-mode hardening (8),
README/PRD/design.md/architecture.md/demo video (9).

**Phase 5 locked three semantics that later phases must not undo:**

- **Provider selection is per session and immutable.** Chosen in `POST /api/sessions`, stamped
  on the row, used by every turn. There is no PATCH, and `MessageCreate` carries no provider
  field. Changing provider means creating a new session.
- **No automatic substitution.** A dead provider ends the stream in a terminal `error`. No
  fallback path exists in the backend or the client, and that absence is what the test asserts.
- **A failed `grounding` verdict is a retraction**, not a footnote — the answer is struck through
  under a banner naming the fabricated quotes and invalid tags. Sources and verdicts are persisted
  (`messages.sources`, `messages.grounding`), so both survive a reload.

**Phase 6 locked four more:**

- **An essay is written from a verified answer, or not at all.** Abstentions (422), failed and
  NULL verdicts (409) are refused server-side — 1,250 confident words built on a known
  fabrication would launder a failure into a more shareable artifact.
- **Evidence is rehydrated by `chunk_id`, never re-searched.** A missing chunk (`ingest --force`
  replaces chunk ids) fails with `evidence_unavailable` rather than silently swapping different
  material under the same `[E#]` labels.
- **Trust rules are code-owned; craft is skill-owned.** `app/prompts/ship30_rules.md` goes to Pi
  as `--system-prompt`, `SKILL.md` as `--append-system-prompt`. Editing the skill changes how an
  essay reads and can never relax grounding.
- **The word target is measured and reported, never enforced.** Truncating would sever quotes and
  citation tags mid-sentence, turning verified prose into a fabrication.

[docs/verification-matrix.md](docs/verification-matrix.md) is the **status of record** at
assignment level — one row per requirement, with executed-test evidence. Read it rather than
trusting a summary, and update it when a row's evidence changes; its **Known gaps** section is the
live TODO list. The Artifact Viewer is split across two rows on purpose: integration (the pane,
Phase 5, done) and rendering/isolation (Phase 7, not started).

Reasoning lives in [docs/](docs/) — `agent-framework-comparison.md`, `agent-layer-decision.md`,
`retrieval-calibration.md`, `ship30-essays.md`. Work happens on a phase branch
(`phase-6-ship30`), commits read `feat(phase-N): …`, matrix updated in the same commit.

## Commands

```bash
# Full stack — db + api in containers, migrations run in the API entrypoint.
# Ollama is deliberately HOST-NATIVE, not containerised (GPU passthrough on
# Windows/WSL2 is fragile). Start it separately: `ollama serve`.
docker compose up

# Container-side test suite, against the real runtime image (profile-gated,
# so a plain `up` never starts it). BUILD FIRST — `run` never rebuilds.
docker compose --profile test build api-tests
docker compose --profile test run --rm api-tests
```

The `test` stage COPYs `data/transcripts/`, `spike/evidence/` and `spike/results/` as offline
fixtures. All three are git-ignored, so **that build fails on a fresh clone** — the first needs
ingestion, the `spike/` two are never in a clone. `docker compose up` (target `runtime`) copies
none of them and is unaffected.

Host dev loop (a venv already exists at `backend/.venv`):

```bash
cd backend && uvicorn app.main:app --reload      # API on :8000
cd frontend && npm install                        # not vendored; Docker installs its own copy
cd frontend && npm run dev                        # Vite on :5173, proxies /api to :8000
cd frontend && npm run build                      # tsc -b + vite -> frontend/dist (Docker copies it to app/static)
```

Tests, ingestion, migrations, calibration — all from `backend/`:

```bash
python -m pytest -q                                       # whole suite
python -m pytest tests/test_agent.py                      # one file
python -m pytest -k test_context_does_not_leak_between_sessions   # one test

python -m app.ingest [--force] [--slug S] [--limit N]     # ~45s, needs Ollama + network
alembic upgrade head
alembic revision --autogenerate -m "message"
python -m tests.eval.run_calibration                      # re-score the frozen eval set
```

Ingestion is **explicit, never automatic on boot** — it would make `docker compose up` slow and
couple API liveness to the embedding model.

**No linter, formatter or type-checker exists on the Python side** — `requirements-dev.txt` is
pytest, pytest-asyncio, anyio. The frontend has no test runner either (Phase 5 kept one out; its
browser driver lives outside the repo), so `npm run build` — `tsc -b` — is the only frontend gate.
Do not add tooling to either side silently.

## Architecture

Boundary chain, as actually implemented (skill 03):

```
routers/chat.py   transport only — SSE framing, no queries, no provider decisions
  repository.py   all persistence; session-scoped by construction
  agent.py        orchestration
    retrieval.py  a database query, NOT an agent tool
    providers.py  the provider seam
      pi_runtime.py   Pi subprocess, JSON-lines events
    grounding.py  quote/citation verification
  ship30.py       essay generation — content transformation, NOT another
                  branch inside agent.py (skill 03 separates the two)
```

**Two invariants in [backend/app/agent.py](backend/app/agent.py) are structural, not prompted:**

1. *No evidence, no answer.* When retrieval returns nothing the model is never invoked;
   `ABSTENTION` is a module constant. Abstention is not something the model can decline.
2. *Every answer is verified.* `verify_answer` runs on all generated output, unconditionally —
   not gated on provider, model, or config.

**The provider seam.** `ModelProvider.stream()` is not overridden by subclasses: every provider
generates through `PiRuntime`, which makes "switch provider by configuration" true by construction
rather than convention. A concrete provider owns only its `check()` health probe (direct HTTP, so
health never depends on the agent framework) and its `pi_provider` name. `get_provider()` selects
by config; no business logic branches on provider identity.

Pi's own provider config is [deploy/pi-models.json](deploy/pi-models.json), baked in at
`/home/appuser/.pi/agent/models.json`, credential-free by construction. **DeepSeek is deliberately
absent from it** — it is a *built-in* Pi provider resolving `DEEPSEEK_API_KEY` by exact name, so a
custom `deepseek` entry shadows the built-in and breaks credential resolution (measured, Phase
4.5). The Ollama entry's `maxTokens: 3072` is a container-only cap sized on Phase 1's measurement
of this essay task (2,220 output tokens); 2,048 truncates.

**Configuration.** [backend/app/config.py](backend/app/config.py) is the *only* place
environment is read — nothing else may call `os.environ`. `Settings.redacted()` defines what is
safe to log or return over HTTP. `env_file=(".env", "../.env")`, so a host run from `backend/`
picks up the repo-root `.env`.

**Error taxonomy** ([backend/app/errors.py](backend/app/errors.py)): one envelope for every
failure — `{"error": {code, message, retryable, request_id}}` — with each `AppError` subclass
owning its `code`, HTTP status and `retryable`. A new failure mode gets a subclass, never an
ad-hoc `HTTPException`; the frontend branches on `error.code`, and no stack trace reaches the
client. `ResourceConflict` (409) is split from `DatabaseUnavailable` (503) because an
`IntegrityError` *is* a `SQLAlchemyError`, and collapsing them once reported a healthy database as
an outage. Phase 8 extends this taxonomy rather than starting a second one.

**Retrieval determinism** is a correctness property: exact search (`ORDER BY embedding <=> query`,
no ANN index, 100% recall by construction) plus a total tie-break
`(distance, transcript_id, chunk_index)`. `RETRIEVAL_MIN_SIMILARITY=0.40` and
`RETRIEVAL_MAX_PER_SOURCE=2` were set by a **pre-registered** calibration
([docs/retrieval-calibration.md](docs/retrieval-calibration.md)) — question set committed before
the run. Changing either value without re-running it invalidates the eval set.

**Data model** ([backend/app/models.py](backend/app/models.py)): `sessions` / `messages` with a
unique `(session_id, seq)` index, the sequence allocated atomically in
`repository.append_message`; `transcripts` / `chunks` with a pgvector `embedding` column and a
`content_hash` that drives the re-ingest skip. `list_messages` is session-scoped and has no
unscoped variant.

**The corpus is pinned, not vendored.** The upstream archive carries no licence and this repo is
public, so [manifest.json](backend/app/corpus/manifest.json) pins identity (source commit + slug)
and integrity (per-episode sha256) and [fetch.py](backend/app/ingest/fetch.py) downloads into
**git-ignored** `data/transcripts/` at ingest time, refusing on a hash mismatch rather than
indexing unverified content. So a clone has zero transcripts, **ingestion needs network once**,
and `corpus_ready` skips until it has run.

**SSE protocol** ([backend/app/routers/chat.py](backend/app/routers/chat.py)):
`meta → sources → delta* → grounding → done | error`. `sources` precedes any text on purpose —
citations are evidence the system retrieved, not claims the model made, so they are trustworthy
before a token is generated. Verification necessarily lands after the text, which is why a
failed verdict is a retraction (see Phase 5 above).

**Frontend** ([frontend/src/](frontend/src/)): `api.ts` owns HTTP + the SSE reader, `useChat.ts`
owns the per-session state machine (`sending → sourced → streaming → verifying → done | retracted
| abstained | error`), and `components/` render it. Two orderings drive the layout: citations
render *above* the answer because the `sources` event precedes the first token, and the verdict
*below* it because verification cannot precede the text. `retracted` and `abstained` are
deliberately distinct from `error` — one is an untrustworthy answer, one is the system correctly
declining. Abstention on replayed history is *derived* (`sources == [] && grounding != null`),
sound only because of the "no evidence, no answer" invariant above.

`useEssay.ts` reuses that same `TurnState` machine — an essay streams the same protocol — adding
only an elapsed clock, since a ten-minute local generation without one is indistinguishable from a
hang. `components/ArtifactPane.tsx` is Phase 7's surface: it renders `essay.markdown` as a React
text child in `<pre>`, so the browser never parses it as markup. **0 `dangerouslySetInnerHTML`, 0
`iframe`, 0 `innerHTML`, no Markdown library** is an asserted property with a matrix row behind
it — Phase 7 changes it deliberately and must re-evidence that row.

**API surface:** `/api/health`, `/api/health/live`, `/api/config`, `/api/providers[/check]`,
`POST /api/providers/probe`, `/api/sessions` (CRUD + `/{id}/messages` + `/{id}/essays`),
`/api/essays/{id}`, `/api/retrieval/search`, `/api/retrieval/status`. `/api/health` reports real
dependency state — DB down is `unhealthy` (503), provider down is `degraded` (200), because an
operator needs to tell a broken deployment from an unstarted Ollama.

## Mandated stack (non-negotiable — from the assignment)

| Concern | Requirement | Current |
|---|---|---|
| Backend | FastAPI | ✅ |
| Agent layer | Claude Agent SDK **or** Pi Coding Agent | ✅ Pi — SDK rejected on measurement (24,472-token harness vs a locked 8,192 context); see [docs/agent-framework-comparison.md](docs/agent-framework-comparison.md) |
| Persistence | PostgreSQL — conversations, session ids, timestamps, user metadata | ✅ pgvector/pg18 |
| Cloud LLM | Anthropic Claude or OpenAI (DeepSeek via the Anthropic-compatible endpoint) | ✅ `deepseek` |
| Local LLM | **Ollama, mandatory** — the demo must run on it | ✅ `qwen3:4b-instruct` |
| Provider switching | Per session, selected provider visible, fallback documented | ✅ backend + UI |
| Ship 30 essays | ~1,250 words, grounded, in the Artifact Viewer | ✅ Phase 6 — verified live on Ollama (1,338 words, 254.7s); see [docs/ship30-essays.md](docs/ship30-essays.md) |
| Knowledge base | Lenny's Podcast transcripts with traceable attribution | ✅ 20 episodes pinned in [manifest.json](backend/app/corpus/manifest.json), fetched at ingest time — **not** vendored |
| Startup | One command | ✅ `docker compose up` |
| Config | `.env.example`, required vs optional marked, no committed secrets | ✅ |

Sessions must maintain **independent context** and must not leak context across sessions.

## Environment gotchas — measured, not folklore

- **`127.0.0.1`, never `localhost`** for Ollama. `localhost` resolves `::1` first, Ollama binds
  IPv4 only: 2032ms vs 0.53ms per new connection.
- **`OLLAMA_CONTEXT_LENGTH=8192` is locked** to a 4 GB GTX 1650 (8192 → 4.1 GB / 33s;
  32768 → 8.0 GB / 173s). Raising it re-opens a measurement, and it is the reason the Claude
  Agent SDK could not be used.
- **Compose must pin `target: runtime`.** The Dockerfile's last stage is `test`; unpinned builds
  shipped pytest as the API entrypoint.
- **Postgres 18 mounts at `/var/lib/postgresql`**, not `/var/lib/postgresql/data` — the old path
  hard-errors. Named volume only, never a Windows bind mount.
- **`PI_WORKING_DIR` must stay outside the repo.** Pi injects project context files (`CLAUDE.md`,
  `AGENTS.md`) discovered from its cwd — measured at +1,311 tokens on every request, i.e. this
  very file, 16% of the context budget, invisible unless you count tokens.
- **Pi exits 0 on failure.** Unreachable endpoints, bad model ids and rejected credentials all
  return 0; `message.stopReason == "error"` is the only reliable signal.
- **Pi always runs `--no-tools`.** Retrieval is deterministic application code; the agent needs
  no tool surface, and read/write/edit/bash in a web backend is a live liability.
- **`docker compose --profile test run` does NOT rebuild.** It served a cached pre-Phase-5 image
  for two phases, freezing the container count while the host suite grew. Build first, and check
  the collected count.
- **Pi's `--skill` cannot deliver a skill body under `--no-tools`.** Skills are
  progressive-disclosure: only name+description enter the system prompt, the body arrives via the
  `read` tool. Use `--system-prompt` / `--append-system-prompt`, which read a file when the
  argument is a path — and note Pi silently uses the path *as prompt text* when the file is
  missing, so verify existence before spawning. `ship30.py` looks in
  `app/skills/05-ship30-writing/SKILL.md` (image) then `.claude/skills/…` (host), overridable by
  `SHIP30_SKILL_PATH`; that order is what lets one authored copy serve both.
- **Changing `EMBEDDING_MODEL`/`EMBEDDING_DIM` requires a full re-ingest** — vector spaces are
  incompatible.

## Tests

Integration tests against a **real PostgreSQL**, not SQLite — the substitute would not exercise
JSONB, the CASCADE, or the unique `(session_id, seq)` index.

- `TEST_DATABASE_URL` points at a scratch database, and `conftest.py` refuses to run unless its
  name contains `test` — the fixtures call `drop_all()`. Read from the process environment first,
  then `.env`, then the Compose defaults.
- The ingested corpus is a **read-only shared fixture** in the dev database, reached via a
  separate `CORPUS_DATABASE_URL` (defaults to `DATABASE_URL`). Corpus sessions roll back on exit.
- `corpus_ready`, `ollama_ready`, `pi_ready` **skip loudly** when the corpus is not ingested or
  Ollama/Pi are absent. A green run full of skips is not a pass — check the skip count.

## Project skills — read the relevant one before touching its area

`.claude/skills/` holds six hand-written skills encoding the working agreement:

- **`01-oogway-fde`** — governing skill. Before any major feature: user problem → smallest useful
  solution → assumptions → failure modes → acceptance criteria → implement/test/document. Prefer
  simple architecture, few dependencies, explicit interfaces, deterministic behavior.
- **`02-rag-grounding`** — ingestion shape and the chunk metadata contract (`source_id`,
  `source_title`, `speaker`, `source_url`, `transcript_id`, `chunk_id`, publication date).
  Retrieve *evidence*, not merely similar text.
- **`03-agent-architecture`** — keep deterministic logic, retrieval, model interaction, tools, and
  content transformation separate. No recursive agents, no hidden state. Agent execution must
  expose selected model, selected skill/tool, retrieval status, errors, latency.
- **`04-llm-provider-routing`** — depend on the common interface, select via config, never
  hardcode provider choice. Handle missing key, Ollama unavailable, model unavailable, timeout,
  malformed response. Log provider/model/duration/outcome — never keys.
- **`05-ship30-writing`** — ~1,250 words, Hook → setup → tension → insight → explanation →
  application → takeaway, every substantive claim traceable to retrieved evidence. **This file is
  live prompt input**: `app/ship30.py` passes it to Pi as `--append-system-prompt` and stamps its
  sha256 on every essay, so editing it changes output with no code change — which is the point.
  The grounding rules live in `app/prompts/ship30_rules.md`; an edit here cannot relax them.
- **`06-artifact-security`** — generated HTML/CSS is untrusted input. Explicit isolation and/or
  sanitization strategy with a stated permit/block/strip policy an evaluator can read. If an
  artifact cannot be rendered safely, do not render it — surface the reason and log it.

`.agents/skills/thermo-nuclear-code-quality-review/` is a vendored third-party skill (pinned in
`skills-lock.json`, `disable-model-invocation: true`). Do not modify it; invoke only when asked.

## Non-negotiable behaviors

- **Never hide failures.** Detectable, logged, surfaced, recoverable where possible. Missing keys,
  unavailable Ollama, model timeouts, empty retrieval and DB failures degrade visibly rather than
  crashing or silently substituting.
- **No fabricated citations or quotes**, ever — the core trust property of the product.
- **Session isolation** is a correctness requirement, not a nicety.
- A feature is not complete until it exists, tests pass, **failure modes are tested**, and the docs
  — including [docs/verification-matrix.md](docs/verification-matrix.md) — are updated.
