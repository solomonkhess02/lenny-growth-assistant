# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

This is a **greenfield take-home assignment repo**. It currently contains only the assignment
spec (`Forward_Deployed_Engineer_Take_Home_Assignment.docx`) and the skill definitions under
`.claude/skills/`. There is no application source, no package manifest, no test runner, and no
git repository yet — everything below describes what is being built, not what exists.

**When scaffolding begins, replace this section with the real build/run/test commands.**

## What is being built

"The Lenny Growth Assistant" — a full-stack conversational web app that ingests Lenny's Podcast
transcripts, answers product/growth questions grounded in them with source attribution, turns
those answers into Ship 30 for 30-style essays, and renders generated Markdown/HTML artifacts in
an in-app Artifact Viewer beside the chat.

Deadline: **25/08/26 EOD**. The evaluator must be able to clone and run the solution using only
the documented steps.

## Mandated stack (non-negotiable — from the assignment)

| Concern | Requirement |
|---|---|
| Backend | FastAPI |
| Agent layer | Anthropic Claude Agent SDK **or** Pi Coding Agent |
| Persistence | PostgreSQL (Supabase or Railway acceptable) — conversations, session IDs, timestamps, user metadata |
| Cloud LLM | At least one (Anthropic Claude or OpenAI) |
| Local LLM | **Ollama, mandatory** — the submitted demo must run on it |
| Provider switching | Via configuration only; selected provider visible in UI/config; fallback documented |
| Knowledge base | Lenny's Podcast / Newsletter transcripts, with traceable source attribution |
| Startup | One command — Docker Compose or equivalent |
| Config | `.env.example` with safe defaults, required vs optional marked; no committed secrets |

Sessions must maintain **independent context** and must not leak context across sessions.

## Required deliverables

Beyond working code, the submission is graded on these artifacts — treat them as first-class
work products, not afterthoughts:

1. Public GitHub repo, sensible structure, no secrets
2. `README.md` — architecture, prerequisites, install, env vars, local + cloud model setup, run, tests, troubleshooting
3. **PRD** — user/problem, success metric, assumptions, scope in/out, flows, acceptance criteria, risks, plan
4. `design.md` — UI/UX principles, information architecture, interaction states, responsive behavior, accessibility
5. `architecture.md` — DB schema, API endpoints, component boundaries, ingestion/retrieval flow, agent routing, model toggle, security, deployment topology
6. **Agent transcripts** in a dedicated folder — including failed attempts and how they were corrected, secrets scrubbed
7. Tests — automated coverage of API, retrieval, routing, persistence + a manual UI test plan
8. 2–3 min demo video (camera on, shows local Ollama, covers one technical trade-off)

## Project skills — read these before implementing

`.claude/skills/` holds six hand-written skills that encode the working agreement for this
project. They are short; read the relevant one before touching its area.

- **`01-oogway-fde`** — the governing skill. Before any major feature: identify the user problem,
  define the smallest useful solution, state assumptions, state failure modes, define acceptance
  criteria, then implement/test/document. Prefer simple architecture, few dependencies, explicit
  interfaces, deterministic behavior. Never claim a feature complete until implementation exists,
  tests pass, failure modes are tested, and docs are updated.
- **`02-rag-grounding`** — the ingestion pipeline shape and chunk metadata contract
  (`source_id`, `source_title`, `speaker`, `source_url`, `transcript_id`, `chunk_id`, publication
  date). Retrieve *evidence*, not merely similar text. Never fabricate citations or quotes; when
  evidence is insufficient, say the transcript material does not support the answer.
- **`03-agent-architecture`** — keep deterministic logic, retrieval, model interaction, tool
  invocation, and content transformation separate. Boundary chain: API → session → agent →
  retrieval → tools/skills → model → persistence. No recursive agents, no hidden state. Agent
  execution must expose selected model, selected skill/tool, retrieval status, errors, latency.
- **`04-llm-provider-routing`** — depend on a common model interface (`CloudProvider`,
  `OllamaProvider`), select via env/config, never hardcode provider choice in business logic.
  Handle missing API key, Ollama unavailable, model unavailable, timeout, malformed response.
  Log provider, model, duration, outcome — never keys or secrets.
- **`05-ship30-writing`** — ~1,250 words, Hook → setup → tension → insight → explanation →
  application → takeaway. Every substantive factual claim traceable to retrieved evidence.
  Returns Markdown suitable for the Artifact Viewer.
- **`06-artifact-security`** — generated HTML/CSS is untrusted input. Pick an explicit isolation
  and/or sanitization strategy and document the rationale; the viewer needs a stated
  permit/block/strip policy an evaluator can read. If an artifact cannot be rendered safely, do
  not render it — surface the reason and log it.

`.agents/skills/thermo-nuclear-code-quality-review/` is a vendored third-party skill (pinned in
`skills-lock.json`, `disable-model-invocation: true`) for strict maintainability audits. It is
kept separate from the project skills — do not modify it, and invoke it only when explicitly asked.

## Non-negotiable behaviors

- **Never hide failures.** Failures must be detectable, logged, surfaced, and recoverable where
  possible. Handle missing keys, unavailable Ollama, model timeouts, empty retrieval, and DB
  connection failures gracefully rather than crashing or silently degrading.
- **No fabricated citations or quotes**, ever — this is the core trust property of the product.
- **Session isolation** is a correctness requirement, not a nicety.
