# Requirements-to-evidence matrix

One row per assignment requirement: requirement → acceptance criteria → evidence → status.

**Evidence means an executed test or a performed manual step, never "the code looks correct."**
Where a requirement is not yet met, the row says so rather than being omitted.

- **Status key:** ✅ met with evidence · 🟡 partially met · ⬜ not started (phase not reached)
- Test names are runnable: `cd backend && python -m pytest -k <name>`
- Current suite: **172 passed, 0 failed** (2026-08-25)

---

## §3.1 Mandated stack

| Req | Acceptance criteria | Evidence | Status |
|---|---|---|---|
| Backend is FastAPI | App serves, routes registered | `tests/test_health_and_errors.py` (9) | ✅ |
| PostgreSQL persistence | Conversations, session ids, timestamps, user metadata persisted | `tests/test_sessions.py` (8); migration `0001` | ✅ |
| Agent layer: Claude Agent SDK | SDK drives text, tools, skills | Phase 1 T1/T2/T3 on both providers | 🟡 proven in spike; wired in Phase 4 |
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
| Grounded answers with citations | Model answers cite retrieved evidence | — | ⬜ Phase 4 |
| No fabricated citations/quotes | Fabrication detected mechanically | `tests/test_grounding.py` (17), incl. real Phase 1 fabrications | ✅ harness; gate applied Phase 4 |

## §4 Product capabilities

| Req | Acceptance criteria | Evidence | Status |
|---|---|---|---|
| Q&A grounded in transcripts | Answerable → cited answer | Retrieval half proven (38 eval tests) | 🟡 generation in Phase 4 |
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
| 7 | Tests (API, retrieval, routing, persistence) + manual plan | 🟡 172 automated; manual plan below |
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
| M12 | Ask an answerable question in the UI | Cited answer | ⬜ Phase 5 |
| M13 | Ask an unsupported question | Explicit abstention, no fabrication | ⬜ Phase 4/5 |
| M14 | Follow-up question | Resolves against prior turn | ✅ at retrieval level |
| M15 | New session | No bleed from previous session | ✅ verified |
| M16 | Generate a Ship 30 essay on Ollama | ~1,250 words, renders | ⬜ Phase 6 |
| M17 | Script-bearing HTML artifact | Handled per stated policy | ⬜ Phase 7 |
| M18 | Kill Ollama mid-request | Structured, legible, logged error | ⬜ Phase 8 |

## Known gaps

1. **No `README.md`.** The repo is public and renders bare. Highest-value missing artifact.
2. **M9 never performed.** Citation deep links are verified *structurally* (URL built from stored `start_seconds`, text confirmed present in the source file) but no one has clicked one and watched the video land on the quoted sentence. That is the one claim in the citation chain resting on construction rather than observation.
3. **Calibration margin is thin** — +0.031 on n=25. See `retrieval-calibration.md`.
4. **Attribution 11/16 at top-1.** Two supported questions miss their expected episode entirely. Reported, not tuned away.
5. **Agent-transcripts folder (deliverable 6) not created.** Decisions and corrections are recorded in the plan and `docs/`, but not in the required layout.
6. **Ingestion needs network once** to fetch the pinned corpus. Must be a documented README prerequisite alongside `ollama pull`.
