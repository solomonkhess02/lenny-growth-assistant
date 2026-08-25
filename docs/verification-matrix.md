# Requirements-to-evidence matrix

One row per assignment requirement: requirement → acceptance criteria → evidence → status.

**Evidence means an executed test or a performed manual step, never "the code looks correct."**
Where a requirement is not yet met, the row says so rather than being omitted.

- **Status key:** ✅ met with evidence · 🟡 partially met · ⬜ not started (phase not reached)
- Test names are runnable: `cd backend && python -m pytest -k <name>`
- Current suite: **263 passed, 0 failed, 0 skipped** (2026-08-25, Phase 5)

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
| Selected provider visible | Exposed via API **and UI** | `GET /api/providers`, `/api/config`; header pill shows the active session's provider on every turn incl. replayed history | ✅ |
| Fallback documented | Documented, and **no silent substitution** | `test_a_failing_provider_is_surfaced_never_substituted` — dead provider ends the stream in `provider_unavailable` and the other provider's name appears nowhere in it; no client-side fallback path exists | ✅ enforced |

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
| Artifact Viewer — integration | Pane present, open/close, empty + loading states | `frontend/src/components/ArtifactPane.tsx` | ✅ Phase 5 (layout/plumbing only) |
| Artifact Viewer — rendering | Renders Markdown/HTML side by side | — | ⬜ Phase 7 (needs Phase 6 content) |
| Artifact isolation | Stated permit/block/strip policy | Decision D-4 recorded. Phase 5 renders **no** untrusted content — no `dangerouslySetInnerHTML`, no iframe — so the pane cannot outrun its policy | ⬜ Phase 7 |

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
| M9 | Open a citation `citation_url` in a browser | Video opens at the quoted moment | ✅ **verified 2026-08-25** — clicked `[E1]` in the running app; opened `youtube.com/watch?v=_CCwoQZH5hI&t=418`; player read **6:58 / 1:28:31** on the cited episode and `video.currentTime === 418`. Chunk at 418s matches transcript line 96 `(00:06:58)` |
| M10 | Stop Ollama, retry search | Actionable error naming `ollama serve` | ✅ covered by test; not manually rehearsed |
| M11 | Stop db container, hit API | 503 `database_unavailable` | ✅ verified in Phase 2B |
| M12 | Ask an answerable question in the UI | Cited answer | ✅ **verified in-browser 2026-08-25** — 2 citation cards rendered, answer cites `[E1]`/`[E2]` inline, `✓ Verified against sources`, 28.9 s |
| M13 | Ask an unsupported question | Explicit abstention, no fabrication | ✅ **verified in-browser** — own neutral state (0.1 s), no error styling, no citations; DOM probe: 0 `.turn-error`, 0 `.verdict.fail` |
| M14 | Follow-up question | Resolves against prior turn | ✅ at retrieval level |
| M15 | New session | No bleed from previous session | ✅ verified |
| M16 | Generate a Ship 30 essay on Ollama | ~1,250 words, renders | ⬜ Phase 6 |
| M17 | Script-bearing HTML artifact | Handled per stated policy | ⬜ Phase 7 |
| M18 | Kill Ollama mid-request | Structured, legible, logged error | ⬜ Phase 8 |
| M19 | Provider indicator matches the active session | Header names the session's provider + model, and its health | ✅ **verified in-browser** — deepseek session under `LLM_PROVIDER=ollama` shows `deepseek · deepseek-v4-pro`; a degraded session shows `ollama · qwen3:4b-instruct · unavailable` |
| M20 | Retry does not change provider | Reissue stays on the session's provider | ✅ **verified in-browser** — provider unreachable; header identical before/after Retry; across every message stream the only provider named was `ollama`, never the healthy `deepseek` |
| M21 | Citations/grounding replay after reload | Reload restores evidence and verdict | ✅ **verified in-browser** — post-reload view pixel-identical to live, incl. citations, 28.9 s latency and verdict |
| M22 | Artifact pane layout / open / close | Pane renders, collapses, restores; no untrusted HTML | ✅ **verified in-browser** — empty state + Hide/Show; renders no generated content (Phase 7 owns isolation) |

## Phase 5 additions

Provider selection is per session and **immutable**: chosen at creation, fixed for every turn,
never mutated. Changing provider means creating a new session — there is no route or control
that does otherwise.

| Req | Acceptance criteria | Evidence | Status |
|---|---|---|---|
| Provider chosen at session creation | `SessionCreate.provider`; session stamps provider+model | `test_session_can_select_a_provider_other_than_the_default` | ✅ |
| Unknown provider rejected cleanly | 422 `validation_failed`, not a 500 | `test_unknown_provider_is_422_not_500` | ✅ |
| Default unchanged when omitted | Falls back to `LLM_PROVIDER` | `test_omitted_provider_falls_back_to_configuration` | ✅ |
| Turn runs on the **session's** provider | `meta.provider` follows the session, not config | `test_turn_runs_on_the_sessions_provider_not_the_configured_one`; verified live (deepseek session under `LLM_PROVIDER=ollama`) | ✅ |
| Provider is immutable | No per-message override; no PATCH/PUT route | `test_message_body_cannot_carry_a_provider`, `test_no_route_mutates_an_existing_sessions_provider` | ✅ |
| Sessions do not contaminate each other | Two providers, two live sessions, each stamped correctly | `test_sessions_on_different_providers_do_not_contaminate_each_other` | ✅ |
| **No automatic substitution** | Dead provider → terminal error; other provider never named | `test_a_failing_provider_is_surfaced_never_substituted` | ✅ |
| Citations persist | Stored sources == streamed sources | `test_grounded_answer_persists_its_citations`; verified live via `GET /api/sessions/{id}` | ✅ |
| Verdict persists | FAIL survives reload with its details | `test_failed_verdict_is_persisted_not_dropped`; live replay derived state `retracted` | ✅ |
| Abstention persists distinctly | `sources: []` + a recorded clean verdict | `test_abstention_persists_empty_sources_and_a_clean_verdict` | ✅ |
| Unverified ≠ verified-clean | `grounding` NULL on user turns, not a PASS-shaped default | `test_user_turns_carry_no_verdict` | ✅ |
| Migration is reversible | upgrade → downgrade → upgrade; legacy rows survive | Scratch DB with a pre-Phase-5 row: survived, `sources` → `[]`, `grounding` → NULL | ✅ |
| Sources render before text | Citation cards precede the answer in the DOM | **Measured in-browser**: at the instant `.citations` appeared, `.msg.assistant .body` count was **0**; screenshot shows evidence + “Evidence found · generating…” with no answer text | ✅ |
| Failed verdict retracts | Struck-through, dimmed, banner names what failed | **Verified in-browser** on a replayed FAIL row: computed style `text-decoration-line: line-through`, `opacity: 0.55`; banner “Answer retracted — it failed verification” listing both fabricated quotes and `[E7]` | ✅ |
| Abstention is not an error | Own state and styling, no error colour | **Verified in-browser** — neutral grey note, 0 error elements in the DOM | ✅ |
| Frontend type gate | `npm run build` clean | `tsc -b && vite build` → 40 modules, 0 errors | ✅ |

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
2. ~~M9 never performed~~ — **RESOLVED (2026-08-25).** A citation was clicked in the running app and followed to YouTube: correct episode, player at **6:58**, `video.currentTime === 418`, matching transcript line 96 `(00:06:58)`. The citation chain no longer rests on construction alone.
3. **Calibration margin is thin** — +0.031 on n=25. See `retrieval-calibration.md`.
4. **Attribution 11/16 at top-1.** Two supported questions miss their expected episode entirely. Reported, not tuned away.
5. **Agent-transcripts folder (deliverable 6) not created.** Decisions and corrections are recorded in the plan and `docs/`, but not in the required layout.
6. ~~Agent SDK not used~~ — **RESOLVED.** Pi Coding Agent adopted as the §3.1 agent
   framework. Cost accepted knowingly: local latency 24.2 s → 36.4 s, cloud 3.9 s → 19.8 s,
   from Node process startup per request. `--mode rpc` would amortise it.
7. ~~Streamed text is provisional until the `grounding` event~~ — **ADDRESSED (Phase 5).**
   Verification still cannot precede the text it verifies, but the UI now treats a failed
   verdict as a retraction: the answer is struck through and dimmed under a banner naming the
   fabricated quotes and invalid tags. `GroundingBanner.tsx`.
8. ~~UI does not yet render sources or grounding~~ — **RESOLVED (Phase 5).** Citations render
   from the `sources` event *before* any text, and the verdict renders after it. Both are now
   persisted (`messages.sources`, `messages.grounding`, migration `0003`) and survive reload —
   verified live, including a FAILED verdict replaying as a retraction.
9. ~~Pi is not provisioned in the Docker image~~ — **RESOLVED (Phase 4.6).** Pi and a Node 22
   runtime are baked into the image; `deploy/pi-models.json` is installed at
   `/home/appuser/.pi/agent/models.json`. Verified by cold build and in-container generation on
   both providers. See `agent-framework-comparison.md` §14.
10. **Ingestion needs network once** to fetch the pinned corpus. Must be a documented README prerequisite alongside `ollama pull`.

11. ~~Phase 5 UI verified structurally, not visually~~ — **RESOLVED (2026-08-25).** Driven in
    headless Chromium against the real Docker stack: citations-before-text measured in the DOM,
    retraction confirmed by computed style, abstention confirmed as a non-error state, provider
    indicator and retry checked against a deliberately unreachable provider, reload replay
    confirmed, artifact pane open/close confirmed, and M9 followed to the video. Screenshots
    were reviewed. The driver lives outside the repo (scratchpad) — **no** test framework was
    added to `frontend/package.json`, per the Phase 5 constraint.
