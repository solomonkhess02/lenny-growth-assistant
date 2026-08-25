# Agent transcripts

How this system was actually built with a coding agent — including the attempts that were wrong,
and what corrected them.

## Why these are curated rather than raw

The raw material is 27 Claude Code session logs, roughly 32 MB of JSONL. Two reasons they are not
committed verbatim:

1. **Secrets.** A scan found the live `DEEPSEEK_API_KEY` present verbatim in one session log. The
   deliverable requires removing secrets before committing, and mechanically scrubbing 32 MB of
   tool output, file dumps and environment echoes is a weaker guarantee than authoring the text
   that gets committed.
2. **Legibility.** A raw transcript is mostly tool calls. What is worth reading is the decision
   trail: what was attempted, what broke, what the evidence was, and what changed as a result.

So each file below is a narrative of one phase, with verbatim commands, measurements and error
text where those are the point. Nothing here is reconstructed from memory — every number is one
recorded in [`docs/`](../docs/), the
[verification matrix](../docs/verification-matrix.md), or a commit message at the time.

## The working method

Six hand-written skills in [`.claude/skills/`](../.claude/skills/) encode the working agreement
the agent operated under. The governing one, `01-oogway-fde`, requires the same sequence before any
major feature: **user problem → smallest useful solution → assumptions → failure modes →
acceptance criteria → implement/test/document.**

Two rules did most of the work in practice:

- **Evidence means an executed test or a performed manual step, never "the code looks correct."**
  This is why the matrix has a status column with ❌ in it.
- **A feature is not complete until failure modes are tested.** Most entries below are failures
  found because something had to be *proven* rather than reviewed.

## What went wrong, at a glance

The corrections worth reading. Each links to its phase.

| # | The mistake | How it was caught |
|---|---|---|
| 1 | Planned to use the Claude Agent SDK before measuring its context cost | [Phase 1](phase-01-provider-spike.md) — counted tokens: 24,472 against a locked 8,192 |
| 2 | Used `localhost` for Ollama, as anyone would | [Phase 2](phase-02-transport-and-skeleton.md) — measured 2032 ms vs 0.53 ms |
| 3 | Allocated message sequence numbers with a read-then-write race | [Phase 2](phase-02-transport-and-skeleton.md) — self-review before the phase closed |
| 4 | An episode parsed to zero turns and was silently skipped | [Phase 3](phase-03-ingestion-and-retrieval.md) — counted rows and found the gap |
| 5 | Trusted Pi's exit code | [Phase 4](phase-04-agent-layer.md) — it returns 0 on failure. Bad keys "succeeded" |
| 6 | Delivered the writing skill via `--skill` | [Phase 4](phase-04-agent-layer.md) — the body never arrived under `--no-tools` |
| 7 | Added a `deepseek` entry to Pi's model config | [Phase 4](phase-04-agent-layer.md) — it shadowed the built-in and broke auth |
| 8 | Let Pi's working directory sit inside the repo | [Phase 4](phase-04-agent-layer.md) — it injected `CLAUDE.md` into every prompt, +1,311 tokens |
| 9 | Left the Compose build target unpinned | [Phase 4.6](phase-04-6-docker-agent-path.md) — shipped **pytest** as the API entrypoint |
| 10 | Believed the container test count | [Phase 4.6](phase-04-6-docker-agent-path.md) — `run` never rebuilds; it was two phases stale |
| 11 | Kept a prompt fix that lowered the fabrication rate | [Phase 6](phase-06-ship30-essays.md) — **reverted**: it changed no verdict |
| 12 | Finished essays vanished after several minutes | [Phase 6](phase-06-ship30-essays.md) — asyncio's 64 KiB line limit, found by measuring the event |
| 13 | Set a strict CSP and assumed it was correct | [Phase 7](phase-07-artifact-isolation.md) — it silently unstyled the artifact frame |
| 14 | Monkeypatched `os.name` in a test | [Phase 8](phase-08-failure-hardening.md) — broke `pathlib` everywhere else |
| 15 | Reported partial output as an ordinary answer | [Phase 8](phase-08-failure-hardening.md) — the retraction property had a hole in it |

## Phase index

| Phase | File | Outcome |
|---|---|---|
| 1 | [Provider and local-model spike](phase-01-provider-spike.md) | Agent framework chosen by measurement; local essay ceiling predicted |
| 2 | [Transport and skeleton](phase-02-transport-and-skeleton.md) | Two environment constants locked; a concurrency bug fixed before it shipped |
| 3 | [Ingestion and retrieval](phase-03-ingestion-and-retrieval.md) | Silent corpus loss made unrepresentable; thresholds pre-registered |
| 4 | [Agent layer on Pi](phase-04-agent-layer.md) | Four undocumented Pi behaviours found the hard way |
| 4.6 | [Docker agent path](phase-04-6-docker-agent-path.md) | Two deployment defects that only appear in the container |
| 5 | [Chat UI](phase-05-chat-ui.md) | Trust semantics locked; verified visually, not just structurally |
| 6 | [Ship 30 essays](phase-06-ship30-essays.md) | A measured model limit, reported rather than tuned away |
| 7 | [Artifact isolation](phase-07-artifact-isolation.md) | A silent CSP failure caught only in a real browser |
| 8 | [Failure-mode hardening](phase-08-failure-hardening.md) | Four failure modes that were wrong, not merely absent |

## A note on what the agent was good and bad at

**Good at:** mechanical breadth — writing the 401-test suite, keeping the error taxonomy
consistent, applying a decided pattern across many files without drift.

**Bad at, consistently:** believing things that sound right. Every entry in the table above is a
case where plausible reasoning produced a wrong answer and only a measurement caught it —
`localhost` really does look equivalent to `127.0.0.1`, a lower fabrication rate really does look
like an improvement, and a strict CSP really does look strictly better. The value of the working
method was not that it made the agent smarter; it was that it kept forcing claims into a form where
being wrong was detectable.
