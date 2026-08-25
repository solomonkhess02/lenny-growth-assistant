# Architecture

How The Lenny Growth Assistant is built, for someone who has to run, debug or extend it.

The design is organised around one property: **an answer that has not been verified against its
evidence must never be able to read as one that has.** Most of the decisions below are downstream
of that, and where the code looks more rigid than necessary, this is usually why.

**Contents:** [Database schema](#1-database-schema) · [API endpoints](#2-api-endpoints) ·
[Component boundaries](#3-component-boundaries) ·
[Ingestion and retrieval](#4-ingestion-and-retrieval-flow) · [Agent routing](#5-agent-routing) ·
[Model toggle](#6-model-toggle) · [Security](#7-security) ·
[Deployment topology](#8-deployment-topology)

---

## 1. Database schema

PostgreSQL 18 with pgvector. Five tables in two groups: conversation state
(`sessions`, `messages`, `essays`) and the knowledge base (`transcripts`, `chunks`). Four Alembic
revisions, applied automatically on API start.

```
sessions ──┬──< messages ──┐            transcripts ──< chunks
           │   (CASCADE)   │ (SET NULL)                 (CASCADE)
           └──< essays ────┘
               (CASCADE)
```

### `sessions`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `title` | varchar(200) NULL | |
| `user_metadata` | JSONB NOT NULL `{}` | §3.1 requires user metadata be persisted. No auth in this build, so this is an opaque bag (client label, user agent), not an identity |
| `provider` | varchar(32) NOT NULL | **Immutable after creation** |
| `model` | varchar(128) NOT NULL | **Immutable after creation** |
| `created_at` / `updated_at` | timestamptz NOT NULL | |

**Why provider/model are immutable.** No route mutates them and `MessageCreate` carries no
provider field. That is what lets these two columns be read as a true record of what produced every
turn in the session, rather than a snapshot of whatever configuration happened to hold at creation
time. Switching provider means creating a new session.

### `messages`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `session_id` | UUID NOT NULL → `sessions.id` **ON DELETE CASCADE** | A message can never exist outside a session |
| `seq` | integer NOT NULL | Monotonic within a session |
| `role` | varchar(16) NOT NULL | CHECK `IN ('user','assistant','system')` |
| `content` | text NOT NULL | |
| `provider` / `model` / `latency_ms` | NULL | Per-turn provenance |
| `sources` | JSONB NOT NULL `[]` | The evidence this turn was built from |
| `grounding` | JSONB **NULL** | The verification verdict |
| `created_at` | timestamptz NOT NULL | |

**`UNIQUE INDEX ix_messages_session_seq (session_id, seq)`** — the sequence is allocated atomically
in `repository.append_message`. Ordering by timestamp is unreliable when two rows land in the same
millisecond, and concurrent posts to one session must not collide.

**Two nullability decisions carry meaning:**

- `sources` is populated from stored chunk rows, never from model output — so a replayed citation
  is exactly as trustworthy as a live one, and an answer cannot lose its attribution because the
  reader refreshed the page.
- `grounding` is **nullable, and NULL is not PASS.** NULL means no verdict was recorded (a user
  turn, or a turn interrupted before verification). Collapsing those two states is precisely how an
  unverified answer would come to look like a verified one.

### `essays`

An essay is deliberately **not** a `messages` row with a `kind` discriminator. It must not appear
in the transcript, must not enter conversation history for retrieval, and must be addressable on
its own for the Artifact Viewer. A discriminator would have produced all three problems at once in
exchange for saving a table.

Carries the same trust columns as `messages` (`sources`, `grounding`, `provider`, `model`) plus:

| Column | Type | Notes |
|---|---|---|
| `source_message_id` | UUID NULL → `messages.id` **ON DELETE SET NULL** | Deleting one message must not silently destroy a finished artifact. Deleting the *session* still takes both |
| `markdown` | text NOT NULL | |
| `format` | varchar(16) NOT NULL `markdown` | Explicit so rendered HTML never becomes ambiguous |
| `word_count` | integer NOT NULL | CHECK `>= 0`. **Measured, never enforced** — truncating to a target would sever quotes and citation tags mid-sentence, turning verified prose into a fabrication |
| `skill_name` / `skill_sha256` | NOT NULL | Provenance for the *instructions*, not just the model: the digest pins the exact revision of `SKILL.md` that produced this essay |

### `transcripts` and `chunks`

`slug` is the identifier that ties everything together — manifest key, on-disk filename, table row,
and the `source_id` in every citation. One name end to end means a citation can always be walked
back to its exact source file.

| `chunks` column | Notes |
|---|---|
| `transcript_id` → `transcripts.id` CASCADE | Re-ingesting cannot strand chunks |
| `chunk_index` | UNIQUE with `transcript_id` |
| `speaker` | Per **chunk**, not per transcript — 4 of the 20 episodes have three or more speakers |
| `text` | |
| `start_seconds` / `end_seconds` | CHECK `end >= start`, `start >= 0`. `start_seconds` is what makes a citation independently checkable by a human: `youtube_url + "&t=<start_seconds>"` |
| `embedding` | `Vector()` — **dimensionless on purpose** |
| `embedding_model` / `embedding_dim` | Stored so a model change is *detectable* |

Two constraints encode defects that actually happened:

- **`CHECK turn_count > 0`** exists because one episode once parsed to zero turns and was excluded
  with no error. A corpus that is quietly incomplete produces confident wrong answers, so the
  failure is now unrepresentable.
- **`embedding` is dimensionless** because exact search uses no ANN index, and only an index
  requires a fixed width. Switching embedding model is then an env change plus a re-ingest rather
  than a schema migration. The cost is that the column will happily store 384- and 768-wide vectors
  side by side, so `embedding_model`/`embedding_dim` are the guard the database cannot be.

---

## 2. API endpoints

18 endpoints, all under `/api`. One FastAPI process also serves the built React app.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Real dependency state. DB down → `unhealthy` **503**; provider down → `degraded` **200** |
| `GET` | `/api/health/live` | Process liveness only, no dependencies — this is what Compose's healthcheck uses |
| `GET` | `/api/config` | Effective non-secret configuration (`deepseek_api_key_present`, never the key) |
| `GET` | `/api/providers` | Configured providers and their models |
| `GET` | `/api/providers/check` | Live health probe |
| `POST` | `/api/providers/probe` | Probe a supplied configuration without persisting it |
| `POST` | `/api/sessions` | Create. **Provider is chosen here and only here** |
| `GET` | `/api/sessions` | List |
| `GET` | `/api/sessions/{id}` | Detail |
| `DELETE` | `/api/sessions/{id}` | Delete, cascading to messages and essays |
| `GET` | `/api/sessions/{id}/messages` | Replay history, including sources and verdicts |
| `POST` | `/api/sessions/{id}/messages` | **Ask a question — SSE stream** |
| `POST` | `/api/sessions/{id}/essays` | **Write an essay — SSE stream** |
| `GET` | `/api/sessions/{id}/essays` | List this session's essays |
| `GET` | `/api/essays/{id}` | One essay, with its Markdown |
| `GET` | `/api/essays/{id}/render` | **Sanitized HTML** for the Artifact Viewer |
| `GET` | `/api/retrieval/search` | Inspect retrieval directly — evidence without generation |
| `GET` | `/api/retrieval/status` | Corpus counts and embedding-space compatibility |

**There is no `PUT` or `PATCH` on sessions.** Its absence is the enforcement of the immutable
provider rule — not an oversight.

### Streaming protocol

Both generation endpoints stream Server-Sent Events in a fixed order:

```
meta → sources → delta* → grounding → done | error
```

**`sources` precedes any generated text on purpose.** Citations are evidence the system retrieved,
not claims the model made, so they are trustworthy before a single token exists. Verification
necessarily lands *after* the text it verifies, which is why a failed verdict is presented as a
**retraction** of what the reader has already seen rather than a footnote.

A turn that fails after partial text emits **no** `grounding` event at all — never a fabricated
verdict — and the client marks that text explicitly unverified.

### Error taxonomy

One envelope for every failure:

```json
{"error": {"code": "...", "message": "...", "retryable": true, "request_id": "..."}}
```

Each subclass in [`errors.py`](backend/app/errors.py) owns its code, HTTP status and retryability.
A new failure mode gets a subclass, never an ad-hoc `HTTPException`; the frontend branches on
`code`, and no stack trace ever reaches a client.

| Code | HTTP | Retryable |
|---|---|---|
| `not_found` | 404 | no |
| `validation_failed` | 422 | no |
| `conflict` | 409 | **yes** |
| `evidence_unavailable` | 409 | no |
| `database_unavailable` | 503 | **yes** |
| `provider_unavailable` | 503 | **yes** |
| `provider_misconfigured` | 500 | no |
| `generation_timeout` | 504 | **yes** |
| `artifact_render_failed` / `artifact_unsafe` | 500 | no |
| `artifact_too_large` | 413 | no |
| `artifact_unsupported_format` | 422 | no |
| `internal_error` | 500 | no |

`conflict` (409) is split from `database_unavailable` (503) because an `IntegrityError` *is* a
`SQLAlchemyError` — collapsing them once reported a perfectly healthy database as an outage.

---

## 3. Component boundaries

```
routers/chat.py     transport only — SSE framing. No queries, no provider decisions.
  repository.py     all persistence; session-scoped by construction
  agent.py          orchestration
    retrieval.py    a database query, NOT an agent tool
    providers.py    the provider seam
      pi_runtime.py Pi Coding Agent as a subprocess, JSON-lines events
    grounding.py    quote and citation verification
  ship30.py         essay generation — content transformation, a separate path
  artifacts.py      pure function; the ONLY place untrusted Markdown becomes HTML
```

**Retrieval is a database query, not an agent tool.** The model does not decide whether to search,
what to search for, or when to stop. That makes evidence selection deterministic and reviewable,
and it is what allows the abstention rule below to be structural.

**Two invariants in [`agent.py`](backend/app/agent.py) are structural, not prompted:**

1. **No evidence, no answer.** When retrieval returns nothing, the model is never invoked;
   `ABSTENTION` is a module constant. Abstention is not a behaviour the model can decline.
2. **Every answer is verified.** `verify_answer` runs on all generated output, unconditionally —
   not gated on provider, model or configuration.

`ship30.py` is a sibling of `agent.py`, not a branch inside it: answering a question and
transforming an answer into an essay are different operations with different evidence rules.

**Configuration discipline.** [`config.py`](backend/app/config.py) is the only place configuration
is read; nothing else calls `os.environ` for a setting. The single exception is
`PiRuntime.child_env()`, which copies the parent environment to build the **subprocess** env and
then pops `DEEPSEEK_API_KEY`, so an ambient key can never leak into a provider that should not see
it. That is credential isolation, not config reading.

**Frontend** ([`frontend/src/`](frontend/src/)) mirrors the same seams: `api.ts` owns HTTP and the
SSE reader, `types.ts` declares the `TurnState` union, `useChat.ts`/`useEssay.ts` own the state
machines, and `components/` only render. See [design.md](design.md).

---

## 4. Ingestion and retrieval flow

### Ingestion — explicit, never automatic

```
manifest.json ──► fetch ──► sha256 verify ──► parse ──► chunk ──► embed ──► chunks
 (pinned: commit    (network,   (refuse on     (turns,   (speaker-  (Ollama)
  + slug + hash)     once)       mismatch)      speakers) aware)
```

Run with `python -m app.ingest [--force] [--slug S] [--limit N]`. It is a deliberate manual step:
doing it at boot would make startup slow and would couple API liveness to the embedding model.

**The corpus is pinned, not vendored.** The upstream archive carries no licence and this repo is
public, so [`manifest.json`](backend/app/corpus/manifest.json) pins identity and integrity, and
[`fetch.py`](backend/app/ingest/fetch.py) downloads into git-ignored `data/transcripts/` at ingest
time, **refusing on a hash mismatch** rather than indexing unverified content.

**Refresh is idempotent and atomic.** `content_hash` plus `embedding_model` drives the skip; an
unchanged episode is not re-embedded. A failed re-ingest leaves the previous version intact rather
than a half-replaced episode.

Every chunk carries the full attribution contract — `source_id`, `source_title`, `speaker`,
`source_url`, `transcript_id`, `chunk_id`, publication date — verified across all 1,395 chunks with
zero violations.

### Retrieval — determinism as a correctness property

```sql
ORDER BY embedding <=> :query_embedding      -- exact, no ANN index, 100% recall
```

Then a **total tie-break** on `(distance, transcript_id, chunk_index)`, so the ordering is fully
determined and the same question returns the same evidence every time. Results below
`RETRIEVAL_MIN_SIMILARITY` are dropped; if nothing survives, the system reports that the
transcripts do not support the question instead of citing its least-bad guess.
`RETRIEVAL_MAX_PER_SOURCE` caps how much any one episode may contribute, so an answer keeps a
corroborating source.

`RETRIEVAL_MIN_SIMILARITY=0.40` and `RETRIEVAL_MAX_PER_SOURCE=2` come from a **pre-registered**
calibration — the question set was committed before the run. Changing either without re-running it
invalidates the eval set. See [docs/retrieval-calibration.md](docs/retrieval-calibration.md).

### Grounding verification

After generation, every quoted span and every `[E#]` citation tag is checked against the retrieved
evidence. A quote that does not appear in the evidence is a fabrication; a tag pointing at
non-existent evidence is invalid. The verdict is persisted, so it survives a reload, and a failure
retracts the answer in the UI.

Known limit, reported rather than hidden: **Markdown blockquotes are outside quote verification.**
The obvious fix was tested and rejected — the real essay's blockquotes are a sources list, so naive
extraction flags honest lines. The essay prompt instructs against blockquote quotation and a
`blockquote_lines` count is reported (0 in every live run).

---

## 5. Agent routing

The agent layer is the **Pi Coding Agent**, invoked as a subprocess that streams JSON-lines events.

The Claude Agent SDK was the alternative and was **rejected on measurement**: its harness consumes
24,472 tokens against a context locked to 8,192 for VRAM reasons, leaving no room for evidence.
Full comparison: [docs/agent-framework-comparison.md](docs/agent-framework-comparison.md).

**Pi always runs `--no-tools`.** Retrieval is deterministic application code, so the agent needs no
tool surface — and read/write/edit/bash in a web backend is a live liability, not a convenience.

Routing is therefore explicit rather than model-driven. There are exactly two paths, chosen by
which endpoint was called:

| Path | Entry | Evidence rule |
|---|---|---|
| **Answer** | `POST /api/sessions/{id}/messages` | Retrieved fresh for the question |
| **Essay** | `POST /api/sessions/{id}/essays` | **Rehydrated by `chunk_id`, never re-searched** |

An essay is written **from a verified answer, or not at all**: abstentions are refused with 422,
failed and NULL verdicts with 409. Twelve hundred confident words built on a known fabrication
would launder a failure into a more shareable artifact.

Evidence rehydration matters for the same reason: if a chunk id no longer exists (`ingest --force`
replaces them), the request fails with `evidence_unavailable` rather than silently swapping
different material under the same `[E#]` labels.

**Trust rules are code-owned; craft is skill-owned.** `app/prompts/ship30_rules.md` goes to Pi as
`--system-prompt`; the writing skill goes as `--append-system-prompt`. Editing the skill changes
how an essay *reads* and can never relax grounding.

### Operational notes about Pi, all measured

- **Pi exits 0 on failure.** Unreachable endpoints, bad model ids and rejected credentials all
  return exit code 0; `message.stopReason == "error"` is the only reliable signal.
- **Its JSON-lines outgrow asyncio's default 64 KiB line limit** — `turn_end`/`agent_end` echo the
  whole conversation including thinking content, measured at 55,027 bytes on a real essay. The
  default silently discarded *finished* essays, so the reader pins a 16 MiB limit and skips-and-logs
  past anything larger.
- **`PI_WORKING_DIR` must stay outside the repository.** Pi injects project context files
  (`CLAUDE.md`, `AGENTS.md`) discovered from its working directory — measured at +1,311 tokens on
  every request, 16% of the context budget, invisible unless you count tokens.

### Generation bounds

An idle bound (`GENERATION_IDLE_TIMEOUT_S`, default 120 s) is the primary mechanism, with a total
wall-clock backstop (`GENERATION_TIMEOUT_S`, default 900 s). The deadline is enforced per read, so
a slow-but-progressing local model never trips it. On timeout the child process — and on POSIX its
whole process group, because Pi is a CLI shim over Node — is killed before the error frame is sent.

---

## 6. Model toggle

**`ModelProvider.stream()` is not overridden by subclasses.** Every provider generates through
`PiRuntime`. That makes "switch provider by configuration" true *by construction* rather than by
convention: there is no per-provider generation path that could drift.

A concrete provider owns only two things: its `check()` health probe — direct HTTP, so health never
depends on the agent framework — and its `pi_provider` name. `get_provider()` selects by
configuration, and **no business logic anywhere branches on provider identity**.

| | Ollama | DeepSeek |
|---|---|---|
| Location | Host-native | Cloud, Anthropic-compatible endpoint |
| Credential | none | `DEEPSEEK_API_KEY` |
| Default model | `qwen3:4b-instruct` | `deepseek-v4-pro` |
| Measured grounded answer | ~24.2 s | ~15.7 s |
| Ship 30 essays | Generate, but **do not pass verification** (0/12 at n=3) | Pass |

Pi's own provider configuration is [`deploy/pi-models.json`](deploy/pi-models.json), baked in at
`/home/appuser/.pi/agent/models.json` and **credential-free by construction** — keys arrive only
through the subprocess environment.

> **DeepSeek is deliberately absent from that file.** It is a *built-in* Pi provider that resolves
> `DEEPSEEK_API_KEY` by exact name, so a custom `deepseek` entry shadows the built-in and breaks
> credential resolution. Measured the hard way.

**Fallback behaviour: there is none, by design.** A dead provider ends the stream in a terminal
`error` naming that provider. No fallback path exists in the backend or the client, and that
absence is what the test asserts. A silent substitution would make the provenance stamped on the
answer false.

---

## 7. Security

### Generated artifacts are untrusted input

Two independent gates, deliberately redundant:

1. **Server-side sanitization** — [`artifacts.py`](backend/app/artifacts.py) renders Markdown with
   `markdown-it-py` (`html=False`) and then runs `nh3` against an explicit allowlist.
2. **Client-side isolation** — the Artifact Pane renders that HTML in a single
   `<iframe sandbox="">`: **no `allow-scripts`, no `allow-same-origin`.**

A sanitizer bug is contained by the sandbox; a sandbox typo is contained by the sanitizer. Neither
is trusted to be sufficient alone.

The application document contains **zero** `dangerouslySetInnerHTML` and **zero** `innerHTML`. The
one iframe is the isolation boundary, not an exception to the rule that untrusted markup never
enters the app document. A retracted essay is confined to escaped source text — the Formatted
toggle is `disabled` for it, so rendering a known fabrication with polish is unreachable rather
than merely discouraged.

Verified in-browser against a fixture essay carrying an inline `<script>`, an `onerror` handler, a
`javascript:` link and an external image: no live script tag, payload present only as inert text,
external image stripped, citation intact, zero network requests to the fixture's host, zero alerts
fired. Full permit/block/strip policy and threat model:
[docs/artifact-isolation.md](docs/artifact-isolation.md).

> **CSP gotcha, measured not assumed.** A `srcdoc` iframe inherits the embedder's CSP. A strict
> app-level `style-src 'self'` silently blocked the essay frame's own typography — caught as a real
> console violation, not reasoned about in the abstract. Fixed by loosening `style-src` alone;
> `script-src` and everything else stayed maximally strict. The failure mode is silent and shows up
> only as unstyled output.

### Credentials

`.env` is git-ignored and `.dockerignore`d; the image never contains it. `Settings.redacted()`
defines what may be logged or returned over HTTP. `PiRuntime.child_env()` pops `DEEPSEEK_API_KEY`
for providers that must not see it. The API key has been scanned against every commit in history
and is absent.

### Data boundaries

Session isolation is structural: every message row carries a NOT NULL `session_id` and every read
path filters on it. `list_messages` is session-scoped and **has no unscoped variant** — there is no
function to call by mistake. Asserted by `test_context_does_not_leak_between_sessions`.

Transcripts are third-party content: referenced by URL and hash, fetched at ingest time, never
redistributed.

---

## 8. Deployment topology

```
┌─ host ─────────────────────────────────────────────────────┐
│                                                            │
│   Ollama  :11434   ← HOST-NATIVE, deliberately not in Docker│
│      ▲                                                     │
│      │ host.docker.internal                                │
│  ┌───┴──────────── docker compose ───────────────────────┐  │
│  │                                                       │  │
│  │   api  :8000   FastAPI + built SPA + Pi + Node        │  │
│  │     │                                                 │  │
│  │     └── db  :5432   pgvector/pgvector:pg18            │  │
│  │              volume: lenny_pgdata → /var/lib/postgresql│ │
│  └───────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

**Ollama is host-native on purpose.** GPU passthrough on Windows/WSL2 is fragile, and host-native
is proven working. The cost is one extra manual step for the evaluator; the benefit is that the
mandated local path actually runs on consumer hardware.

### Image — four stages

| Stage | Base | Role |
|---|---|---|
| `frontend` | `node:22-slim` | Runs `npm run build` **inside the image** |
| `piagent` | `node:22-slim` | Installs the Pi CLI |
| `runtime` | `python:3.13-slim` | Ships. `alembic upgrade head && uvicorn` |
| `test` | extends `runtime` | Container test runner, behind a Compose profile |

The image builds its own frontend and copies `--from=frontend /fe/dist` — never the host's
git-ignored `frontend/dist`. So a fresh clone needs no npm step, and a host build never changes
what the image serves.

**`target: runtime` must stay pinned in Compose.** The Dockerfile's last stage is `test`, and an
unpinned build resolves to it — which once shipped **pytest as the API entrypoint**.

### Operational behaviour

- Migrations run in the API entrypoint. There is no separate migration step.
- `db` has a `pg_isready` healthcheck and `api` waits on `service_healthy`.
- `api`'s healthcheck hits `/api/health/live` — **dependency-free by design**, so a stopped Ollama
  cannot make the container restart-loop.
- Postgres 18 mounts at `/var/lib/postgresql`, **not** `/var/lib/postgresql/data` — the old path
  hard-errors. Named volume only, never a Windows bind mount.
- Logs are structured JSON carrying `request_id`, provider, model, duration and outcome.

> **The container never reads `.env`** — it is `.dockerignore`d, and Compose forwards variables
> explicitly. The retrieval and DeepSeek tuning variables are **not** forwarded, so changing them
> in `.env` affects a host run only. See the README's environment table.

---

## Where the open problems are

[docs/verification-matrix.md](docs/verification-matrix.md) is the status of record: one row per
requirement, each backed by an executed test or a performed manual step, with a **Known gaps**
section listing what is not met. The largest is that Ship 30 essays generated on the local 4B model
do not pass grounding verification — measured at 0 of 12, documented rather than tuned away, and
visible in the product as a retraction.
