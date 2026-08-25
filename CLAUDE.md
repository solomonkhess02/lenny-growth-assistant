# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

"The Lenny Growth Assistant" — a full-stack conversational web app that ingests Lenny's Podcast
transcripts, answers product/growth questions grounded in them with source attribution, turns
those answers into Ship 30 for 30-style essays, and renders generated Markdown/HTML in an
in-app Artifact Viewer beside the chat. Take-home assignment; deadline **26 August 2026 EOD (IST)**; the
evaluator must be able to clone and run it from the documented steps alone.

## Current status — read before planning any work

**Phases 1–5 are complete and verified.** Backend, transcript ingestion, deterministic
retrieval, grounding verification, the Pi agent layer, the Docker agent path, and the Phase 5
chat UI all ship and are covered by the suite (263 passed, 0 skipped, as of 2026-08-25).

**Do not redo a completed phase unless a regression is demonstrated** — reproduce it first,
then fix it.

**Phase 6 is next: Ship 30 essay generation.** Then artifact isolation (7), failure-mode
hardening (8), README/PRD/design.md/architecture.md/demo video (9).

**Phase 5 locked three semantics that later phases must not undo:**

- **Provider selection is per session and immutable.** Chosen in `POST /api/sessions`, stamped
  on the row, used by every turn. There is no PATCH, and `MessageCreate` carries no provider
  field. Changing provider means creating a new session.
- **No automatic substitution.** A dead provider ends the stream in a terminal `error`. There is
  no fallback path in the backend or the client, and that absence is what the test asserts.
- **A failed `grounding` verdict is a retraction**, not a footnote — the answer is struck
  through under a banner naming the fabricated quotes and invalid tags. Sources and verdicts are
  persisted (`messages.sources`, `messages.grounding`), so both survive a reload.

[docs/verification-matrix.md](docs/verification-matrix.md) is the **status of record** at
assignment level — one row per requirement, with executed-test evidence. Read it rather than
trusting a summary, and update it when a row's evidence changes. The Artifact Viewer is split
across two rows on purpose: integration (the pane, Phase 5, done) and rendering/isolation
(Phase 7, not started).

## Commands

```bash
# Full stack — db + api in containers, migrations run in the API entrypoint.
# Ollama is deliberately HOST-NATIVE, not containerised (GPU passthrough on
# Windows/WSL2 is fragile). Start it separately: `ollama serve`.
docker compose up

# Container-side test suite, against the real runtime image (profile-gated,
# so a plain `up` never starts it).
docker compose --profile test run --rm api-tests
```

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

python -m app.ingest [--force] [--slug S] [--limit N]     # ~45s, needs Ollama
alembic upgrade head
alembic revision --autogenerate -m "message"
python -m tests.eval.run_calibration                      # re-score the frozen eval set
```

Ingestion is **explicit, never automatic on boot** — it would make `docker compose up` slow and
couple API liveness to the embedding model.

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
```

**Two invariants in [backend/app/agent.py](backend/app/agent.py) are structural, not prompted:**

1. *No evidence, no answer.* When retrieval returns nothing the model is never invoked;
   `ABSTENTION` is a module constant. Abstention is not something the model can decline.
2. *Every answer is verified.* `verify_answer` runs on all generated output, unconditionally —
   not gated on provider, model, or config.

**The provider seam.** `ModelProvider.stream()` is not overridden by subclasses: every provider
generates through `PiRuntime`. That is what makes "switch provider by configuration" true by
construction rather than convention. A concrete provider owns only its `check()` health probe
(direct HTTP, so health never depends on the agent framework) and its `pi_provider` name.
`get_provider()` selects by config; no business logic branches on provider identity.

**Configuration.** [backend/app/config.py](backend/app/config.py) is the *only* place
environment is read — nothing else may call `os.environ`. `Settings.redacted()` defines what is
safe to log or return over HTTP.

**Retrieval determinism** is a correctness property: exact search (`ORDER BY embedding <=> query`,
no ANN index, 100% recall by construction) plus a total tie-break
`(distance, transcript_id, chunk_index)`. `RETRIEVAL_MIN_SIMILARITY=0.40` and
`RETRIEVAL_MAX_PER_SOURCE=2` were set by a **pre-registered** calibration
([docs/retrieval-calibration.md](docs/retrieval-calibration.md)) — the question set was
committed before the calibration ran. Changing either value without re-running it invalidates
the eval set.

**Data model** ([backend/app/models.py](backend/app/models.py)): `sessions` / `messages` with a
unique `(session_id, seq)` index, the sequence allocated atomically in
`repository.append_message`; `transcripts` / `chunks` with a pgvector `embedding` column and a
`content_hash` that drives the re-ingest skip. `list_messages` is session-scoped and has no
unscoped variant.

**SSE protocol** ([backend/app/routers/chat.py](backend/app/routers/chat.py)):
`meta → sources → delta* → grounding → done | error`. `sources` precedes any text on purpose —
citations are evidence the system retrieved, not claims the model made, so they are trustworthy
before a token is generated. Verification necessarily lands after the text, which is why a
failed verdict is a retraction (see Phase 5 above).

**Frontend** ([frontend/src/](frontend/src/)): `api.ts` owns HTTP + the SSE reader,
`useChat.ts` owns the per-session state machine
(`sending → sourced → streaming → verifying → done | retracted | abstained | error`), and
`components/` render it. Two orderings drive the layout: citations render *above* the answer
because the `sources` event precedes the first token, and the verdict renders *below* it
because verification cannot precede the text. `retracted` and `abstained` are deliberately
distinct from `error` — one is an untrustworthy answer, one is the system correctly declining.
Abstention on replayed history is *derived* (`sources == [] && grounding != null`), which is
sound only because of the "no evidence, no answer" invariant above.

**API surface:** `/api/health`, `/api/health/live`, `/api/config`, `/api/providers[/check]`,
`POST /api/providers/probe`, `/api/sessions` (CRUD + `/{id}/messages`), `/api/retrieval/search`,
`/api/retrieval/status`. `/api/health` reports real dependency state — DB down is `unhealthy`
(503), provider down is `degraded` (200), because an operator needs to tell a broken deployment
from an unstarted Ollama.

## Mandated stack (non-negotiable — from the assignment)

| Concern | Requirement | Current |
|---|---|---|
| Backend | FastAPI | ✅ |
| Agent layer | Claude Agent SDK **or** Pi Coding Agent | ✅ Pi — SDK rejected on measurement (24,472-token harness vs a locked 8,192 context); see [docs/agent-framework-comparison.md](docs/agent-framework-comparison.md) |
| Persistence | PostgreSQL — conversations, session ids, timestamps, user metadata | ✅ pgvector/pg18 |
| Cloud LLM | Anthropic Claude or OpenAI (DeepSeek via the Anthropic-compatible endpoint) | ✅ `deepseek` |
| Local LLM | **Ollama, mandatory** — the demo must run on it | ✅ `qwen3:4b-instruct` |
| Provider switching | Per session, selected provider visible, fallback documented | ✅ backend + UI |
| Knowledge base | Lenny's Podcast transcripts with traceable attribution | ✅ 20 episodes, [data/transcripts/](data/transcripts/) |
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
- **Changing `EMBEDDING_MODEL`/`EMBEDDING_DIM` requires a full re-ingest** — vector spaces are
  incompatible.

## Tests

Integration tests against a **real PostgreSQL**, not SQLite — the substitute would not exercise
JSONB, the CASCADE, or the unique `(session_id, seq)` index.

- `conftest.py` refuses to run unless the database name contains `test`; the fixtures call
  `drop_all()`.
- The ingested corpus is a **read-only shared fixture** in the dev database, reached via a
  separate `CORPUS_DATABASE_URL`. Corpus sessions roll back on exit.
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
  application → takeaway. Every substantive claim traceable to retrieved evidence.
- **`06-artifact-security`** — generated HTML/CSS is untrusted input. Explicit isolation and/or
  sanitization strategy with a stated permit/block/strip policy an evaluator can read. If an
  artifact cannot be rendered safely, do not render it — surface the reason and log it.

`.agents/skills/thermo-nuclear-code-quality-review/` is a vendored third-party skill (pinned in
`skills-lock.json`, `disable-model-invocation: true`). Do not modify it; invoke only when asked.

## Non-negotiable behaviors

- **Never hide failures.** Detectable, logged, surfaced, recoverable where possible. Missing keys,
  unavailable Ollama, model timeouts, empty retrieval, DB failures degrade visibly rather than
  crashing or silently substituting.
- **No fabricated citations or quotes**, ever — the core trust property of the product.
- **Session isolation** is a correctness requirement, not a nicety.
- A feature is not complete until the implementation exists, tests pass, **failure modes are
  tested**, and the docs — including [docs/verification-matrix.md](docs/verification-matrix.md) —
  are updated.
