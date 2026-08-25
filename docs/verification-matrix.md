# Requirements-to-evidence matrix

One row per assignment requirement: requirement → acceptance criteria → evidence → status.

**Evidence means an executed test or a performed manual step, never "the code looks correct."**
Where a requirement is not yet met, the row says so rather than being omitted.

- **Status key:** ✅ met with evidence · 🟡 partially met · ❌ measured and not met · ⬜ not started (phase not reached)
- Test names are runnable: `cd backend && python -m pytest -k <name>`
- Current suite: **401 passed, 0 failed, 1 skipped** on the host; **396 passed, 6 skipped** in the runtime image — **re-verified 2026-08-26 at the close of Phase 9** against a freshly built test image (402 collected on both sides; `npm run build` / `tsc -b` also green). Phase 9 changed no application code, and the counts are unchanged from Phase 8, which is the expected result rather than a coincidence. The host's one skip is `test_posix_cleanup_kills_the_whole_process_group` — `signal.SIGKILL` does not exist on this Windows dev host; it runs (and passes) in the Linux container, which is why the container's skip count did **not** grow despite 17 new Phase 8 tests: the 6 container skips are unchanged from Phase 7 — `Dockerfile` / `.dockerignore` (2, since Phase 4.6) and `frontend/src/**` (4, since Phase 7 — the image ships only the built `dist/`, so the frontend-isolation-invariant tests run on the host).

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
| Ship 30 essay generation | ~1,250 words, correct structure | `tests/test_ship30.py` (30), `tests/test_essays.py` (23). **Live on the mandated path**: Ollama/`qwen3:4b-instruct` in the container, **1,338 words**, within target, 254.7 s. Repeated at n=3 per question: 12/12 essays produced on Ollama, median 1,165–1,189 words, all within target | ✅ Phase 6 — generation |
| Ship 30 essays verify clean locally | A local essay passes grounding often enough to be usable | **0 of 12** Ollama essays passed at n=3 (~20% per-quote fabrication). Prompt mitigation measured and reverted — no verdict change. Model limit, documented not tuned away | ❌ **not met on Ollama** — gap 18 |
| Artifact Viewer — integration | Pane present, open/close, empty + loading states | `frontend/src/components/ArtifactPane.tsx`; composer/scroll layout re-verified in-browser against the Phase 7 sandboxed-iframe geometry (M22, M23, M24) | ✅ |
| Artifact Viewer — rendering | Renders Markdown/HTML side by side | Phase 7: a verified essay renders formatted inside a sandboxed `srcdoc` iframe; a retracted essay stays escaped source. `backend/app/artifacts.py`, `tests/test_artifacts.py` (43), `tests/test_essays.py` (+5). Verified in-browser (M17) | ✅ |
| Artifact isolation | Stated permit/block/strip policy | Decision D-4 recorded in [artifact-isolation.md](artifact-isolation.md). `sandbox=""` (no `allow-scripts`, no `allow-same-origin`) + server-side `nh3`/`markdown-it-py` sanitization + a strict app-level CSP, all three verified in-browser and by test | ✅ |

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
| Model timeout | Surfaced, logged, recoverable | `GenerationTimeout` (504, retryable); idle bound (primary) + total bound (backstop), one `wait_for` per read so a slow-but-progressing turn never trips it: `test_idle_timeout_fires_on_a_silent_provider`, `test_total_timeout_fires_on_a_slow_but_alive_provider`, `test_a_slow_but_progressing_provider_does_not_trip_the_idle_timeout`, `test_a_generation_timeout_ends_the_stream_in_a_retryable_error` (+ essay mirror). Confirmed against **real Ollama** in the rebuilt container with `generation_idle_timeout_s=3` on a real 500-word-essay prompt: fired at 3.5s, code `generation_timeout`, `retryable: true`, zero orphaned processes in `/proc` afterward | ✅ |
| Client disconnect / cancellation mid-generation | Provider process killed deterministically, not left orphaned; a genuine cancellation is logged, never silently dropped | `aclosing(...)` around the provider stream in `agent.py`/`ship30.py` (`stream_answer`/`stream_essay`) plus the routers, replacing GC-dependent cleanup: `test_stream_answer_closes_the_provider_stream_on_early_close`, `test_stream_essay_closes_the_provider_stream_on_early_close`, `test_early_close_kills_a_hung_child_promptly`, `test_posix_cleanup_kills_the_whole_process_group` (process-GROUP kill on POSIX, not just the direct child — Pi is a CLI shim over Node). `asyncio.CancelledError` explicitly caught, logged (`outcome: cancelled`), and re-raised (never swallowed): `test_cancellation_mid_stream_is_logged_and_reraised`. Confirmed against **real Ollama** in the rebuilt container: abandoning an in-flight generation killed the live `pi` process immediately (`aclose()` returned in 0.00s; `/proc` showed zero children afterward) | ✅ |
| DB failure during a stream (not just at the dependency boundary) | Same `database_unavailable`/503/retryable taxonomy as every other route, not a generic `internal_error` | The streaming routes build sessions from `session_factory()` directly (a request-scoped `Depends` cannot outlive the returned response), which bypassed `get_session`'s error mapping entirely. New `db.db_errors()` reuses that exact mapping: `test_db_errors_maps_integrity_error_to_conflict`, `test_db_errors_maps_connection_failure_to_database_unavailable`, `test_db_errors_does_not_touch_unrelated_exceptions`, `test_mid_stream_assistant_write_failure_reports_database_unavailable`, `test_mid_stream_essay_write_failure_reports_database_unavailable` | ✅ |
| Partial output after a mid-stream failure | Never rendered as a verified answer; explicitly marked unverified, distinct from a grounding retraction | Backend: `verify_answer` provably never ran on the interrupted text — `test_a_provider_failure_after_partial_output_emits_no_grounding`, `test_a_provider_failure_after_partial_essay_emits_no_grounding` (no `grounding` event in the stream). Frontend: `Message.tsx`/`ArtifactPane.tsx` apply `.retracted-text` plus a distinct `.unverified-note` banner whenever `state === "error"` with no grounding verdict. **Verified in a real browser 2026-08-26 (M25)** against a real interrupted essay — see M25 for full DOM/computed-style evidence | ✅ |
| Oversized agent event | Finished generation is not discarded; failure is visible | `tests/test_pi_runtime.py` (6 oversized-event tests). 16 MiB line limit + skip-and-log past it; in-container DeepSeek 2 essays/3 crashes → **6 essays/0 crashes** | ✅ |

## §6 Deliverables

| # | Deliverable | Status |
|---|---|---|
| 1 | Public repo, no secrets | ✅ `solomonkhess02/lenny-growth-assistant`, confirmed public (HTTP 200 unauthenticated). Key scanned against every commit → absent |
| 2 | `README.md` | ✅ [README.md](../README.md) — all eight required sections (architecture overview, prerequisites, installation, environment variables, local + cloud model setup, run commands, tests, troubleshooting). The three fresh-clone traps are prerequisites rather than footnotes: host-native Ollama, network-once ingestion, and the `--profile test` build failing by design. Every documented command executed before it was written down |
| 3 | PRD | ✅ [PRD.md](../PRD.md) — §6's fields **plus** §2's Forward Deployment Brief as Part 1, mapping 1:1 onto its five bullets. Success metric is measured, not aspirational; acceptance criterion A11 is left visibly ❌ |
| 4 | `design.md` | ✅ [design.md](../design.md) — principles, IA, the `TurnState` union as the interaction-state spine, design decisions with reasons, and responsive + accessibility positions stated **as measured**, including the artifact pane being unreachable below 1100px and the absence of a contrast/keyboard audit |
| 5 | `architecture.md` | ✅ [architecture.md](../architecture.md) — all eight named sections. Schema documented from the models (5 tables, the unique `(session_id, seq)` index, CASCADE vs SET NULL, the constraints that encode real defects); all 18 endpoints; the error taxonomy table |
| 6 | Agent transcripts (incl. failures) | ✅ [agent-transcripts/](../agent-transcripts/) — index + one file per phase, each *asked → produced → wrong → corrected → evidence*; 15 corrections indexed. Curated rather than raw: the live `DEEPSEEK_API_KEY` appears verbatim in 1 of the 27 local session JSONLs, so authored text is a stronger guarantee than scrubbing 32 MB. Redaction gate run before commit (literal key, `sk-` patterns, credential assignments, DB password, `git check-ignore` on all 10 files) |
| 7 | Tests (API, retrieval, routing, persistence) + manual plan | ✅ 401 automated (host); manual plan M1–M25 in §7 below, linked from the README so an evaluator can find it |
| 8 | Demo video | 🟡 **preparation complete, recording is the author's** — [docs/demo-script.md](demo-script.md): timed 2:30 shot list, narration beats, pre-flight checklist, and the constraint it exists to solve (a 254.7 s local essay cannot be generated on camera inside a 3-minute limit, so content is pre-generated and shown from history, which renders identically because sources and verdicts are persisted) |

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
| M16 | Generate a Ship 30 essay on Ollama | ~1,250 words, renders | ✅ **verified 2026-08-25** — in-container on `qwen3:4b-instruct`: **1,338 words** (within the 1,000–1,500 band), 254.7 s, first token 53.2 s, 4 evidence items (2 carried + 2 added), `blockquote_lines: 0`, no `maxTokens` truncation. Verdict **FAIL** — 3 fabricated quotes of 8 checked, all wholly invented (longest matching prefix: one word) — so the essay was **retracted**, which is the local path behaving as Phase 1 predicted. **Repeated 2026-08-25 at n=3 per question: 6/6 essays produced, 6/6 retracted**, 16 fabricated of 72 checked — see [ship30-essays.md §10](ship30-essays.md) and gap 18 |
| M17 | Script-bearing HTML artifact | Handled per stated policy | ✅ **verified in-browser 2026-08-26** — a fixture essay with an inline `<script>`, an `onerror` handler, a `javascript:` link and an external image, rendered against the built image at `:8000`. App document: zero live-markup occurrences; exactly one `iframe`, `sandbox=""`, no `allow-scripts`/`allow-same-origin`. Inside the frame: no real `<script>` tag, payload present only as inert text, external image replaced by `[image removed]`, `[E1]` intact, 0 network requests to the fixture's external host, 0 JS `alert()` fired. See [artifact-isolation.md §6](artifact-isolation.md) |
| M18 | Unresponsive Ollama mid-request | Structured, legible, logged error; no orphaned process | ✅ **verified 2026-08-26** — not literally killed (this container's Ollama is a real, otherwise-healthy instance also used by other sessions); instead `generation_idle_timeout_s` was tightened to 3s for a one-off run against the real model with a 500-word prompt (well past first-token time on this hardware). Fired at 3.5s: `GenerationTimeout`, code `generation_timeout`, `retryable: true`, logged via `pi_turn_finished` (`outcome: error`). `/proc` inside the container showed zero live `pi`/`node` processes afterward — the same real-subprocess drill repeated for early-close (disconnect): abandoning an in-flight generation killed the live process in `aclose()` (0.00s), zero children left. Verified directly against the real container (process/SSE level) rather than through the UI, since the point here is the mechanism and its cleanup, not the rendering — the UI-facing half of a mid-stream timeout is what M25 separately verifies in a real browser |
| M19 | Provider indicator matches the active session | Header names the session's provider + model, and its health | ✅ **verified in-browser** — deepseek session under `LLM_PROVIDER=ollama` shows `deepseek · deepseek-v4-pro`; a degraded session shows `ollama · qwen3:4b-instruct · unavailable` |
| M20 | Retry does not change provider | Reissue stays on the session's provider | ✅ **verified in-browser** — provider unreachable; header identical before/after Retry; across every message stream the only provider named was `ollama`, never the healthy `deepseek` |
| M21 | Citations/grounding replay after reload | Reload restores evidence and verdict | ✅ **verified in-browser** — post-reload view pixel-identical to live, incl. citations, 28.9 s latency and verdict |
| M22 | Artifact pane layout / open / close | Pane renders, collapses, restores; no untrusted HTML | ✅ **re-verified in-browser 2026-08-26** — empty state + Hide/Show; a formatted essay now renders inside the sandboxed iframe (Phase 7), history sidebar lists multiple essays correctly |
| M23 | Composer stays reachable | Composer visible without scrolling the page, with a long chat AND a long artifact | ✅ **verified in-browser 2026-08-25** (Phase 5/6 layout) and **re-verified 2026-08-26** after the Phase 7 sandboxed iframe changed the pane's internal scroll geometry (`artifacts.py §3`, no `allow-scripts`/`allow-same-origin` means nothing can auto-size the frame). At 1440×900 with 8 chat turns and a long multi-section essay: composer bottom **888px** (within the 900px viewport), page `scrollHeight` **900** (no page scroll) |
| M24 | Chat and artifact scroll independently | Scrolling one does not move the other or the page | ✅ **verified in-browser 2026-08-25** (Phase 5/6) and **re-verified 2026-08-26** against the Phase 7 layout — artifact `scrollTop` 0 → 95 (its full range) while chat scroll and `window.scrollY` stayed unchanged |
| M25 | Mid-stream failure after partial text (Phase 8) | Partial text reads as unverified, never as a normal answer | ✅ **verified in a real browser 2026-08-26** — real Chrome via Playwright driving the built Docker app at `:8000` (`localhost`, not `127.0.0.1`: a second, unrelated process was found bound to `127.0.0.1:8000` on this host, serving a stale config with no Phase 8 settings at all — confirmed by direct comparison, and avoided; `localhost` correctly resolves to the container via Docker's IPv6 forwarding path). No application code was changed: `GENERATION_TIMEOUT_S` was temporarily set to 120s via the Compose environment (restored to 900 immediately after) to interrupt a real Ship 30 essay generation on real Ollama mid-stream, closing the loop from "the mechanism fires" (already shown against a real 500-word prompt in the Model-timeout row above) to "the UI never lets it read as verified." DOM/computed-style evidence, via `page.evaluate`: **(1)** `.essay-body` carries `retracted-text`; computed `opacity: 0.55`, `text-decoration-line: line-through` — 387 words of real generated text struck through and dimmed, not styled like a normal essay. **(2)** `.unverified-note` exists with text "⚠ Unverified — generation ended before this essay could be checked against sources. Treat the text above as provisional, not a confirmed essay."; computed `display: block`, `visibility: visible`, `opacity: 1`; a real non-zero bounding box (329×80px) — genuinely rendered and occupying layout, not hidden or zero-sized. **(3)** `.turn-error` shows `generation_timeout — Generation for provider 'ollama' exceeded the 120s total time limit. Retryable on this session, and therefore on ollama. Nothing is switched for you.` **(4)** `verdictBadgeCount: 0` — zero `.verdict` elements anywhere in the pane. The `grounding` event never fired, so `GroundingBanner` never renders; no verdict, pass or fail, was ever fabricated onto the interrupted text. Full-page screenshot taken; the source *answer* alongside it (a separate, successful turn) correctly shows its own green "✓ Verified against sources" badge in the same screenshot, confirming the unverified styling is specific to the interrupted essay and not a global rendering fault |

## Phase 6 additions

Ship 30 essays. Written **from an existing verified answer**, on the session's provider, from
that answer's own evidence, verified like any other generated text. Full reasoning in
[ship30-essays.md](ship30-essays.md).

| Req | Acceptance criteria | Evidence | Status |
|---|---|---|---|
| Skill reaches the model | `SKILL.md` body is in the system prompt, not merely named | `test_generation_carries_rules_and_skill_separately`. Pi's `--skill` is progressive-disclosure — the body needs the `read` tool, which `--no-tools` removes — so delivery is via `--append-system-prompt` | ✅ |
| Grounding rules are code-owned | An edit to the skill cannot relax them | `test_rules_are_application_owned_not_skill_owned` — each non-negotiable must be in `app/prompts/ship30_rules.md` and **absent** from `SKILL.md` | ✅ |
| Skill is in the runtime image | Present and byte-identical | sha256 `48e0fd16…` matches in image and repo; `TestSkillIsShipped` pins the `.dockerignore` negation and the Dockerfile COPY | ✅ |
| Missing skill fails loudly | Never spawns Pi with a path-as-prompt | `test_missing_skill_file_fails_loudly`. Pi treats a bad path as literal prompt text, so the app must catch it | ✅ |
| No ambient skill discovery | `--no-skills` on every call | `test_skill_discovery_is_always_disabled`. Global `~/.pi/agent/skills` is found regardless of workdir | ✅ |
| Evidence is carried, not re-searched | Same chunks, same labels | `test_carried_evidence_keeps_its_position`, `test_essay_sources_prefix_matches_the_answers` | ✅ |
| Stale evidence refuses | Re-ingest replaces chunk ids → 409, no substitute search | `test_a_missing_chunk_refuses_the_whole_set`, `test_stale_chunk_ids_end_the_stream_with_evidence_unavailable` | ✅ |
| Top-up adds real material | Deeper on episode-specific questions, floor untouched | `test_topup_goes_deeper_on_an_episode_specific_question`. Measured live: evidence 2 → 4, essay 995 → 1,090 words | ✅ |
| No essay from an abstention | 422, naming the reason | `test_abstention_cannot_be_turned_into_an_essay` | ✅ |
| **No essay from a failed verdict** | 409; no generation attempted | `test_failed_verdict_cannot_be_turned_into_an_essay` | ✅ enforced |
| Unverified ≠ verified-clean | NULL grounding refused like a FAIL | `test_unverified_answer_is_refused_like_a_failed_one` | ✅ |
| Cross-session message refused | 404, not 403 | `test_message_from_another_session_is_404_not_403` | ✅ |
| Essay verified unconditionally | Every essay, every provider | `test_every_essay_is_verified` | ✅ |
| Curly **and** straight, at essay length | Both caught in a long Markdown body | `test_fabricated_curly_quote_in_an_essay_is_caught`, `test_fabricated_straight_quote_in_an_essay_is_caught` | ✅ |
| Honest essays still pass | Both quote styles accepted when real | `test_a_genuinely_clean_essay_passes` | ✅ |
| Word target reported, never enforced | No truncation, misses surfaced | `test_nothing_is_truncated_to_hit_the_target`, `test_reported_word_count_is_the_stored_markdown` | ✅ |
| Runs on the session's provider | deepseek session under `LLM_PROVIDER=ollama` | `test_essay_runs_on_the_sessions_provider_not_the_configured_one`; verified live | ✅ |
| **No automatic substitution** | Dead provider → terminal error; other provider never named | `test_a_failing_provider_is_surfaced_never_substituted` (essay mirror) | ✅ |
| Provenance persisted | provider, model, latency, skill + sha256 | `test_essay_is_persisted_with_full_provenance` | ✅ |
| Citations + verdict survive reload | Stored == streamed | `test_citations_and_verdict_survive_a_reload`, `test_failed_verdict_on_an_essay_is_persisted_not_dropped` | ✅ |
| Essays are not turns | Absent from the transcript and from history | `test_essays_do_not_appear_in_the_conversation` | ✅ |
| Session isolation | Essays scoped, cascade on delete | `test_essays_are_isolated_between_sessions`, `test_deleting_a_session_cascades_its_essays` | ✅ |
| Migration reversible | upgrade → downgrade → upgrade | Executed on the dev DB; corpus intact at 1,395 chunks | ✅ |
| Pane renders no untrusted markup | Escaped text only | `grep` over `frontend/src`: 0 `dangerouslySetInnerHTML`, 0 `iframe`, 0 `innerHTML`; no Markdown lib in `package.json` | ✅ |
| Frontend type gate | `npm run build` clean | `tsc -b && vite build` → 41 modules, 0 errors | ✅ |

### Live generations (evidence for M16)

| Path | Words | Within target | Wall clock | First token | Evidence | Verdict |
|---|---:|---|---:|---:|---:|---|
| **Ollama** `qwen3:4b-instruct` (container) | **1,338** | ✅ | 254.7 s | 53.2 s | 4 (2 carried + 2 added) | **FAIL** — 3/8 quotes invented |
| DeepSeek `deepseek-v4-pro` (container) | 1,090 | ✅ | 160.1 s | 144.1 s | 4 (2 + 2) | **FAIL** — 1/26 quotes altered |
| DeepSeek, before the per-source cap fix | 995 | ✗ | 78.6 s | 56.5 s | 2 (2 + 0) | FAIL — 1/22 |

### Repeated at n=3 per question per provider (2026-08-25)

Full method, classification and conclusion in [ship30-essays.md §10](ship30-essays.md). Driven
over the real HTTP path against the container. **No threshold changed; `grounding.py` untouched.**

| Path | attempts | essays produced | PASS | FAIL | fabricated / checked | rate |
|---|---:|---:|---:|---:|---:|---:|
| **Ollama** `qwen3:4b-instruct` | 6 | 6 | **0** | **6** | **16 / 72** | **22.2%** |
| **DeepSeek** `deepseek-v4-pro` | 6 | 2 | 2 | 0 | 0 / **0 quotes** | n/a |

**The DeepSeek row above is superseded — it was distorted by gap 17.** 3 of 6 runs discarded a
*complete* essay to the 64 KiB stream defect, and because event size scales with generation
length, the bug systematically filtered out the *richer* essays. Re-run after the transport fix:

| DeepSeek `deepseek-v4-pro` | attempts | produced | crashes | PASS | fabricated / checked |
|---|---:|---:|---:|---:|---:|
| before the fix | 6 | 2 | **3** | 2 | 0 / **0 quotes** |
| **after the fix** | 6 | **6** | **0** | **6** | **0 / 82 quotes** |

So DeepSeek is genuinely reliable here, not vacuously reliable. **The Ollama findings are
unaffected** — it produced 6/6 with 0 crashes both before and after, because `qwen3:4b-instruct`
emits no thinking content and its events never approached the limit. See
[ship30-essays.md §11.3](ship30-essays.md).

Meanwhile every Ollama short answer PASSed (6/6) while every
Ollama essay FAILed (6/6) on the same evidence and verifier — the failure is specific to essay
length. Of 16 fabricated spans, 14 were wholly invented and 2 altered; **0 came from the prior
answer and 0 crossed a speaker label**, ruling out two implementation hypotheses by measurement.

One prompt-level mitigation (restating the quote rule at the TASK line) was measured at n=3 and
**reverted**: per-quote rate 22.2% → 17.5%, but **0 of 12 essays changed verdict**. Conclusion:
`qwen3:4b-instruct` is not reliable for long-form Ship 30 under a zero-tolerance verifier. See
gap 18.

Both models fabricated on a real Ship 30 task and **both were caught**. Every flagged span was
checked by hand against the evidence: none was a false positive. On Ollama all three were wholly
invented; on DeepSeek the model altered a quote's tense (`"hits a little bit different"` →
`"hit …"`) and coined a scare-quote of its own. This reproduces the Phase 1 finding — qwen3
fabricated 6 of 28 quotes in that era's Ship 30 essay — and is why **retraction is a first-class
screen on the local path, not an edge case**.

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

1. ~~No `README.md`. The repo is public and renders bare.~~ **RESOLVED (2026-08-26, Phase 9.)**
   [README.md](../README.md) ships with all eight required sections, a real screenshot of a
   verified answer on local Ollama, and the three fresh-clone traps promoted to prerequisites.
   Joined by [PRD.md](../PRD.md), [architecture.md](../architecture.md), [design.md](../design.md)
   and [agent-transcripts/](../agent-transcripts/) — deliverables 2–6, all previously ⬜.
2. ~~M9 never performed~~ — **RESOLVED (2026-08-25).** A citation was clicked in the running app and followed to YouTube: correct episode, player at **6:58**, `video.currentTime === 418`, matching transcript line 96 `(00:06:58)`. The citation chain no longer rests on construction alone.
3. **Calibration margin is thin** — +0.031 on n=25. See `retrieval-calibration.md`.
4. **Attribution 11/16 at top-1.** Two supported questions miss their expected episode entirely. Reported, not tuned away.
5. ~~Agent-transcripts folder (deliverable 6) not created.~~ **RESOLVED (2026-08-26, Phase 9.)**
   [agent-transcripts/](../agent-transcripts/) holds an index plus one narrative per phase, each
   *asked → produced → wrong → corrected → evidence*, with 15 corrections indexed and every number
   traceable to `docs/`, this matrix, or a commit message written at the time. Curated rather than
   raw, because the live API key appears verbatim in one of the 27 local session logs — the
   redaction gate is recorded in §6 row 6 above.
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
10. **Markdown blockquotes are outside quote verification.** A fabricated pull-quote in a `>`
    block is never examined. The obvious fix was **tested and rejected**: the real Phase 1 essay's
    blockquotes are a sources list, so naive extraction flags four honest lines and breaks the
    pinned 28/6 assertion. Phase 6 instructs against blockquote quotation and reports a
    `blockquote_lines` count (0 in all three live runs). Closing it properly needs its own
    calibration. See `ship30-essays.md` §5.

11. ~~No generation timeout.~~ **RESOLVED (2026-08-26, Phase 8).** `GenerationTimeout`
    (504, retryable) via an idle bound (primary) plus a total bound (backstop) in
    `pi_runtime.py`, one `wait_for` per read so a slow-but-progressing turn never trips it.
    Confirmed against real Ollama in the rebuilt container (§5 above, M18).

12. **A disconnect still discards a ten-minute generation, unchanged and deliberately so** —
    nothing partial is persisted, because a truncated essay is not an essay (`ship30-essays.md`
    §7). What Phase 8 closed is the **other** half of this gap: the abandoned provider process
    used to keep running, unobserved, until the event loop's async-generator finalizer happened
    to collect it — burning cloud tokens and leaving orphaned local processes with no bound on
    when, or whether, that happened. `aclosing(...)` around the provider stream now makes
    teardown deterministic, and a genuine cancellation (Starlette winning the disconnect race) is
    now logged rather than silently dropped. Confirmed against real Ollama: an abandoned
    generation's process was dead in `/proc` immediately, not eventually. No resumable job still
    does not exist, and is not being proposed.

13. **`deepseek_max_tokens` / `deepseek_disable_thinking` are unwired.** Defined in `config.py`,
    read nowhere. DeepSeek's Phase 1 Test C returned empty after 4,096 output tokens, consistent
    with thinking consuming the budget. Pi exposes `--thinking`; not wired, because the live runs
    did not reproduce the failure. **Deliberately left open by Phase 8**, which added its own
    settings (`generation_idle_timeout_s`/`generation_timeout_s`) rather than touching this one —
    a resilience change and an unrelated config cleanup don't belong in the same commit.

14. **The container test image does not rebuild on `--profile test run`.** It served a cached
    pre-Phase-5 image, which is why "251 passed in the container" was recorded at Phase 4.6 and
    stayed at 251 through Phase 5. `docker compose --profile test build api-tests` is required
    first; the Phase 6 numbers above were taken after a forced rebuild.

15. ~~Ingestion needs network once to fetch the pinned corpus. Must be a documented README
    prerequisite alongside `ollama pull`.~~ **RESOLVED (2026-08-26, Phase 9.)** It is a
    prerequisite row in the README's table with the reason attached (the upstream archive carries
    no licence, so the corpus is pinned by hash and fetched rather than vendored), and a numbered
    installation step. The documented command is
    `docker compose exec api python -m app.ingest` — verified working, so an evaluator needs no
    host Python environment at all.

16. ~~Phase 5 UI verified structurally, not visually~~ — **RESOLVED (2026-08-25).** Driven in
    headless Chromium against the real Docker stack: citations-before-text measured in the DOM,
    retraction confirmed by computed style, abstention confirmed as a non-error state, provider
    indicator and retry checked against a deliberately unreachable provider, reload replay
    confirmed, artifact pane open/close confirmed, and M9 followed to the video. Screenshots
    were reviewed. The driver lives outside the repo (scratchpad) — **no** test framework was
    added to `frontend/package.json`, per the Phase 5 constraint.


17. ~~**Pi's JSON-lines exceed asyncio's 64 KiB line limit, killing finished essays.**~~
    **RESOLVED (2026-08-25).** `pi_runtime` now passes an explicit
    `limit=_STDOUT_LINE_LIMIT` (16 MiB, ~300× the largest event measured) and reads with an
    explicit `readline()` loop, so an event past even that ceiling is logged, counted
    (`oversized_events`) and skipped rather than escaping the generator. Protocol, error taxonomy,
    streaming and cleanup unchanged. 6 regression tests in `tests/test_pi_runtime.py`, all of
    which fail against the pre-fix module with the production exception
    (`LimitOverrunError` → `ValueError`). Verified in-container on the path that failed: the same
    DeepSeek matrix went **2 essays / 3 crashes → 6 essays / 0 crashes**. Original diagnosis
    below.

    **Pi's JSON-lines exceed asyncio's 64 KiB line limit, killing finished essays.**
    `pi_runtime.stream()` iterates `proc.stdout`; `asyncio.StreamReader` caps one line at 64 KiB
    and raises `LimitOverrunError` → `ValueError` past it. Pi's `turn_end`/`agent_end` echo the
    whole conversation **including thinking content** — measured in-container at `agent_end`
    **55,027 bytes** on a real essay prompt (8,656 thinking deltas). **3 of 6 DeepSeek baseline
    runs died this way**, each discarding a complete 6.8–7.8 KB essay after 2–4 minutes, and each
    surfacing to the client as `internal_error`. Pre-existing since Phase 4, not a Phase 6
    regression. Unfixed: it is neither a prompt nor a layout change and needs its own tests.
    `deepseek_disable_thinking` (gap 13) being unwired is what makes it frequent.

18. **Local Ship 30 essays are not reliable, and this is a model limit.** Measured at n=3 per
    question: **0 of 12** Ollama essays passed verification, at ~20% per-quote fabrication and
    12–22 quotations per essay. Prompt mitigation measured and reverted (no verdict change).
    Recommendation: keep the local path exactly as it behaves — a retracted essay is a working
    demonstration of the trust property — and demonstrate a passing essay on DeepSeek while
    stating that its passes often contain no quotations. See [ship30-essays.md §10](ship30-essays.md).
19. **The artifact CSP is not applied to the Vite dev server.** Headers are set by the FastAPI
    response, so they cover the built app at `:8000` — the `docker compose up` / demo path — and
    not `npm run dev` on `:5173`, which injects its own `<style>` tags. Accepted, not fixed: the
    evaluator-facing path is unaffected. See [artifact-isolation.md §3](artifact-isolation.md).
