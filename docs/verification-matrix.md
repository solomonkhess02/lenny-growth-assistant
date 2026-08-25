# Requirements-to-evidence matrix

One row per assignment requirement: requirement → acceptance criteria → evidence → status.

**Evidence means an executed test or a performed manual step, never "the code looks correct."**
Where a requirement is not yet met, the row says so rather than being omitted.

- **Status key:** ✅ met with evidence · 🟡 partially met · ⬜ not started (phase not reached)
- Test names are runnable: `cd backend && python -m pytest -k <name>`
- Current suite: **240 passed, 0 failed** (2026-08-25, Phase 4 + Pi adoption)

---

## §3.1 Mandated stack

| Req | Acceptance criteria | Evidence | Status |
|---|---|---|---|
| Backend is FastAPI | App serves, routes registered | `tests/test_health_and_errors.py` (9) | ✅ |
| PostgreSQL persistence | Conversations, session ids, timestamps, user metadata persisted | `tests/test_sessions.py` (8); migration `0001` | ✅ |
| **Agent layer: Pi Coding Agent** | §3.1 agent framework drives generation | `app/pi_runtime.py`; `tests/test_pi_runtime.py` (38); grounded answers on both providers | ✅ **ADOPTED** |
| Claude Agent SDK (the alternative) | — | Rejected on measurement: 24,472-token harness vs 8,192 locked context. `agent-framework-comparison.md` | n/a — Pi chosen |
| Agent layer boundary | Explicit, no tool surface, grounding enforced | `app/agent.py` (unchanged by adoption); `tests/test_agent.py` (22) | ✅ |
| Cloud LLM | At least one cloud provider works | `DeepSeekProvider`; Phase 1 T1–T3 pass | ✅ |
| **Local LLM (Ollama), mandatory** | Demo runs on Ollama | `OllamaProvider`; embeddings run on Ollama today | ✅ |
| Session isolation | No context leaks across sessions | `test_concurrent_posts_keep_sessions_isolated`, `test_context_does_not_leak_between_sessions` | ✅ |
| One-command startup | `docker compose up` | `docker-compose.yml`; db + api healthy | ✅ |
| `.env.example`, no committed secrets | Placeholders only; real key absent from all history | Key scanned against every commit → absent | ✅ |

## §3.2 Provider switching

| Req | Acceptance criteria | Evidence | Status |
|---|---|---|---|
| Switch by configuration only | No business-logic branch on provider identity | `get_provider()`; `tests/test_providers.py` (13) | ✅ |
| Selected provider visible | Exposed via API/UI | `GET /api/providers`, `/api/retrieval/status` | 🟡 API done; UI in Phase 5 |
| Fallback documented | Documented, and **no silent substitution** | Locked Provider UX contract, req. 5 & 9 | 🟡 documented; enforced in Phase 5 |

## §3.3 Knowledge base

| Req | Acceptance criteria | Evidence | Status |
|---|---|---|---|
| Ingest Lenny's transcripts | All curated episodes ingested, none silently skipped | `ingested=19 skipped=1 failed=0`; DB `zero_turn_files=0` | ✅ |
| **Traceable source attribution** | Every chunk carries the full skill-02 contract | SQL `contract_violations = 0` over 1,395 chunks; `test_every_contract_field_is_populated` | ✅ |
| Citations resolve to real sources | Stored text found in the source file at the stated timestamp | `test_citations_resolve_to_real_source_text`, `test_speaker_is_a_real_speaker_from_that_episode` | ✅ |
| Timestamp-level attribution | Deep link to the exact moment | `test_citation_url_deep_links_to_the_timestamp` | ✅ |
| Refreshable | Idempotent on unchanged content; atomic on change | `tests/test_ingest_refresh.py` (9); full re-run → `skipped=20` | ✅ |
| Grounded answers with citations | Model answers cite retrieved evidence | `test_real_local_model_produces_a_grounded_answer`; manual run on both providers | ✅ |
| No fabricated citations/quotes | Fabrication detected mechanically on **every** answer | `tests/test_grounding.py` (19) + `test_agent.py`; caught a live DeepSeek fabrication | ✅ enforced |

## §4 Product capabilities

| Req | Acceptance criteria | Evidence | Status |
|---|---|---|---|
| Q&A grounded in transcripts | Answerable → cited answer; unsupported → abstain | 38 eval + 22 agent tests; grounded answers on Ollama (24.2s) and DeepSeek (3.9s) | ✅ |
| Ship 30 essay generation | ~1,250 words, correct structure | — | ⬜ Phase 6 |
| Artifact Viewer | Renders Markdown/HTML side by side | — | ⬜ Phase 7 |
| Artifact isolation | Stated permit/block/strip policy | Decision D-4 recorded | ⬜ Phase 7 |

## §5 Resilience (failure modes)

| Failure | Acceptance criteria | Evidence | Status |
|---|---|---|---|
| Missing API key | Structured error, no crash | `ProviderMisconfigured`; `tests/test_providers.py` | ✅ |
| Ollama unavailable | Actionable error naming the fix | `test_embedding_failure_leaves_the_previous_version_intact` | ✅ |
| Model/dimension mismatch | Refused, never silently mixed | `test_model_mismatch_is_refused` | ✅ |
| Empty retrieval | Clean empty result, not an error | `test_empty_result_is_a_clean_list`; 9/9 unsupported → `[]` | ✅ |
| DB connection loss | 503, not 500 | `test_connection_failure_still_maps_to_database_unavailable` | ✅ |
| Constraint conflict | 409, **not** reported as an outage | `test_integrity_error_maps_to_conflict_not_database_unavailable` | ✅ |
| Concurrent writes | No lost updates, no bogus 503s | `tests/test_concurrency.py` (6) | ✅ |
| Corrupt/zero-turn transcript | Fails loudly, names the file, writes nothing | `test_zero_turn_transcript_fails_loudly_and_writes_nothing` | ✅ |
| Corpus integrity mismatch | sha256 refused | `test_integrity_mismatch_is_refused` | ✅ |
| Model timeout | Surfaced, logged, recoverable | — | ⬜ Phase 8 |

## §6 Deliverables

| # | Deliverable | Status |
|---|---|---|
| 1 | Public repo, no secrets | ✅ `solomonkhess02/lenny-growth-assistant` |
| 2 | `README.md` | ⬜ **not started** — repo renders bare |
| 3 | PRD | ⬜ Phase 9 |
| 4 | `design.md` | ⬜ Phase 9 |
| 5 | `architecture.md` | ⬜ Phase 9 |
| 6 | Agent transcripts (incl. failures) | 🟡 decisions recorded in plan + `docs/`; folder not created |
| 7 | Tests (API, retrieval, routing, persistence) + manual plan | 🟡 240 automated; manual plan below |
| 8 | Demo video | ⬜ Phase 9 |

## §7 Manual test plan

Run after `docker compose up` on a clean checkout. Steps marked ⬜ depend on unbuilt phases.

| # | Step | Expected | Status |
|---|---|---|---|
| M1 | `docker compose up -d`, wait for healthy | db + api healthy | ✅ verified |
| M2 | `curl :8000/api/health` | Reports real dependency state | ✅ verified |
| M3 | `python -m app.ingest` | 20/20, ~1,395 chunks, ~53 s, `failed=0` | ✅ verified |
| M4 | Re-run `python -m app.ingest` | `ingested=0 skipped=20 failed=0` | ✅ verified |
| M5 | `python -m app.ingest --slug bogus` | One-line error, exit 2, no traceback | ✅ verified |
| M6 | `GET /api/retrieval/status` | 20 transcripts, 1,395 chunks, `compatible: true` | ✅ verified |
| M7 | `GET /api/retrieval/search?q=How does Duolingo use streaks...` | `supported: true`, deep-linked citations | ✅ verified |
| M8 | `GET /api/retrieval/search?q=How do I make sourdough starter?` | `supported: false`, `count: 0` | ✅ verified |
| M9 | Open a citation `citation_url` in a browser | Video opens at the quoted moment | ⬜ **not performed** — see gaps |
| M10 | Stop Ollama, retry search | Actionable error naming `ollama serve` | ✅ covered by test; not manually rehearsed |
| M11 | Stop db container, hit API | 503 `database_unavailable` | ✅ verified in Phase 2B |
| M12 | Ask an answerable question in the UI | Cited answer | 🟡 API verified; citation rendering is Phase 5 |
| M13 | Ask an unsupported question | Explicit abstention, no fabrication | ✅ verified (48 ms, model never invoked) |
| M14 | Follow-up question | Resolves against prior turn | ✅ at retrieval level |
| M15 | New session | No bleed from previous session | ✅ verified |
| M16 | Generate a Ship 30 essay on Ollama | ~1,250 words, renders | ⬜ Phase 6 |
| M17 | Script-bearing HTML artifact | Handled per stated policy | ⬜ Phase 7 |
| M18 | Kill Ollama mid-request | Structured, legible, logged error | ⬜ Phase 8 |

## Phase 4 additions

| Req | Acceptance criteria | Evidence | Status |
|---|---|---|---|
| No evidence → no answer | Model is **never invoked** when retrieval is empty | `test_abstention_never_invokes_the_model` | ✅ |
| Grounding is mandatory | Every generated answer verified, unconditionally | `test_every_answer_is_verified` | ✅ |
| Model cannot invent citations | Invalid tags detected and surfaced | `test_invented_citation_tag_is_caught` | ✅ |
| Model cannot invent sources | Source cards built from stored rows only | `test_sources_come_from_retrieval_not_the_model` | ✅ |
| Curly **and** straight quotes checked | Both styles extracted and verified | `test_curly_quote_fabrication_is_caught`, `test_straight_quote_fabrication_is_caught` | ✅ |
| Both providers produce grounded answers | Ollama and DeepSeek, config change only | Manual: 24.2 s / 3.9 s, both `PASS` | ✅ |
| Provider selection config-driven | No provider branch in agent code | `test_agent_module_has_no_provider_conditionals` | ✅ |
| Retrieval API preserved | Phase 3 public surface unchanged | 20 retrieval + 38 eval tests unmodified and passing | ✅ |

## Pi adoption (Phase 4 completion)

| Req | Acceptance criteria | Evidence | Status |
|---|---|---|---|
| Pi is the execution path | Generation runs through Pi for both providers | `PiRuntime.stream`; manual: Ollama 36.4 s, DeepSeek 19.8 s, both `PASS` | ✅ |
| Retrieval stays outside Pi | `retrieval.py` untouched; agent has no Pi awareness | `test_agent_layer_has_no_pi_specific_logic` | ✅ |
| No tool surface | `--no-tools` always | `test_command_always_disables_tools`, `test_no_tool_allowlist_is_ever_passed` | ✅ |
| Controlled working directory | Outside repo; no `CLAUDE.md` reachable | `test_workdir_is_outside_the_repository`, `test_workdir_contains_no_project_context_files` | ✅ |
| Key from environment only | Never in `models.json`/`auth.json`/argv/logs | `test_key_is_never_written_to_pi_config_files`, `test_key_never_appears_in_the_command_line` | ✅ |
| Key isolation between providers | Cloud key never handed to the local child | `test_ambient_key_cannot_leak_into_the_local_provider` | ✅ |
| Pi error → application error | `stopReason: "error"` raised, taxonomy-mapped | `test_error_taxonomy_mapping` (8 cases), `test_bad_model_becomes_an_application_error` | ✅ |
| Non-zero failure semantics to FastAPI | Mapped errors carry HTTP status | `test_mapped_errors_carry_http_status_for_fastapi` | ✅ |
| Config-only provider switching | No per-provider generation code | `test_generation_is_not_reimplemented_per_provider`, `test_provider_switch_needs_no_application_code_change` | ✅ |
| Streaming preserved | Incremental `text_delta` | `test_ollama_execution_streams_deltas`; SSE tests unchanged | ✅ |
| Grounding still mandatory | Verification after generation | `test_grounded_answer_through_pi_on_ollama` | ✅ |
| Unsupported never invokes Pi | Zero subprocess spawns | `test_unsupported_question_never_spawns_pi` | ✅ |

## Docker agent path (Phase 4.6)

Verified by **cold build** (`docker compose build --no-cache`) — not from host-installed Pi.

| Req | Acceptance criteria | Evidence | Status |
|---|---|---|---|
| Pi in the API image | CLI present and runnable | `which pi` -> `/opt/pi/bin/pi`; `node --version` -> v22.23.2 | ✅ |
| Container uses its OWN Pi | Not the host install | Container Pi **0.84.3** vs host **0.74.2**; **zero bind mounts** on the container | ✅ |
| Pi config provisioned, no secrets | Credential-free file baked in | `deploy/pi-models.json` -> `/home/appuser/.pi/agent/models.json`; contains only a dummy Ollama apiKey | ✅ |
| Reaches host Ollama | Verified Docker host address | `host.docker.internal:11434`; health probe 50.7 ms, `model_available: true` | ✅ |
| DeepSeek key from env only | Never at rest | Filesystem grep of `/srv`, `/home/appuser`, `/opt/pi` finds nothing; `auth.json` is `{}` | ✅ |
| Compose strips `.env` inline comment | In-container key length 35, not 76 | Asserted in-container: `35` | ✅ |
| Workdir outside the repo | No project context injected | `/tmp/pi-workdir`, outside `/srv`, no `CLAUDE.md` | ✅ |
| Ollama generation in container | Grounded, verified answer | **30.3 s, PASS**, 2 sources, 0 fabricated | ✅ |
| DeepSeek generation in container | Grounded, verified answer | **15.7 s, PASS**, 5 quotes verified, 0 fabricated | ✅ |
| Provider switch in container | Config only, no code change | Both answered from the same call site | ✅ |
| Streaming / grounding / missing key / bad model / Ollama unavailable | Same production-path tests, run in-container | Full suite, **251 passed in the container** | ✅ |

## Known gaps

1. **No `README.md`.** The repo is public and renders bare. Highest-value missing artifact.
2. **M9 never performed.** Citation deep links are verified *structurally* (URL built from stored `start_seconds`, text confirmed present in the source file) but no one has clicked one and watched the video land on the quoted sentence. That is the one claim in the citation chain resting on construction rather than observation.
3. **Calibration margin is thin** — +0.031 on n=25. See `retrieval-calibration.md`.
4. **Attribution 11/16 at top-1.** Two supported questions miss their expected episode entirely. Reported, not tuned away.
5. **Agent-transcripts folder (deliverable 6) not created.** Decisions and corrections are recorded in the plan and `docs/`, but not in the required layout.
6. ~~Agent SDK not used~~ — **RESOLVED.** Pi Coding Agent adopted as the §3.1 agent
   framework. Cost accepted knowingly: local latency 24.2 s → 36.4 s, cloud 3.9 s → 19.8 s,
   from Node process startup per request. `--mode rpc` would amortise it.
7. **Streamed text is provisional until the `grounding` event.** Verification cannot
   precede the text it verifies. A failed verdict arrives after the words are on screen,
   so the UI must render it as a retraction (Phase 5).
8. **UI does not yet render sources or grounding** — the events are emitted and ignored.
9. ~~Pi is not provisioned in the Docker image~~ — **RESOLVED (Phase 4.6).** Pi and a Node 22
   runtime are baked into the image; `deploy/pi-models.json` is installed at
   `/home/appuser/.pi/agent/models.json`. Verified by cold build and in-container generation on
   both providers. See `agent-framework-comparison.md` §14.
10. **Ingestion needs network once** to fetch the pinned corpus. Must be a documented README prerequisite alongside `ollama pull`.
