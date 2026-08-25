# Agent layer decision — SUPERSEDED by Pi adoption

> ## ⛔ STATUS: SUPERSEDED (2026-08-25)
>
> This document explained why Phase 4 shipped generation on the provider seam
> with **no** agent framework, after the Claude Agent SDK proved incompatible
> with our locked 8,192-token local context.
>
> **That gap is now closed.** The project adopted the **Pi Coding Agent** as its
> §3.1 agent framework. Pi's harness overhead is **111 tokens** against the
> SDK's 24,472, so it fits the locked context with room to spare.
>
> - Current decision and evidence → **`agent-framework-comparison.md`**
> - Implementation → **`backend/app/pi_runtime.py`**
>
> The analysis below remains accurate about the **Claude Agent SDK** and is kept
> as the record of why it was rejected. It is **no longer accurate** about the
> project, which is no longer without an agent framework.

---

## Original analysis (Claude Agent SDK rejection)

*Written before Pi was evaluated.*

## The conflict

The assignment mandates two things that cannot both hold on the target hardware:

1. **Agent layer** must use the Anthropic Claude Agent SDK or Pi Coding Agent.
2. **Ollama is mandatory** and *the submitted demo must run on it*.

Phase 4 measured that the Claude Agent SDK cannot run against our locked local
configuration, and that making it fit would break the local demo requirement.

## The measurement

The Agent SDK drives the Claude Code CLI, which injects its own harness prompt
(tool schemas, environment preamble, settings) into every request. Asking it to
reply with a single word:

```
API Error: 400 request (24566 tokens) exceeds the available context size (8192)
```

That overhead is **irreducible**. Three configurations, one trivial prompt:

| Configuration | Prompt tokens |
|---|---|
| `allowed_tools=[]` | 24,561 |
| `allowed_tools=[]`, `setting_sources=[]` | 24,472 |
| the above plus a replacement `system_prompt` | 24,475 |

Disabling every tool, dropping all setting sources, and replacing the system
prompt moved the total by **under 0.4%**. There is no configuration in which
the SDK fits an 8,192-token context.

## Why we do not simply raise the context

`OLLAMA_CONTEXT_LENGTH=8192` is a locked decision, and it was locked on
measurement in Phase 1, not preference:

| Context | Same call | Footprint | CPU offload |
|---|---|---|---|
| 8,192 | **33.3 s** | **4.1 GB** | 45% |
| 32,768 | **173 s** (5.2× slower) | 8.0 GB | 70% |

The GPU is a GTX 1650: 4,096 MiB total, of which **~1,276 MiB is already held
by Windows/display, leaving ~2,660 MiB**. `qwen3:4b-instruct` at 8K is already
45% CPU-offloaded. An 8.0 GB footprint does not fit.

So running the Agent SDK locally would mean a ~24.5K-token prefill on top of
our ~900-token evidence payload, at 32K context, on a card that cannot hold it.
The mandated local demo would take minutes per question. That defeats
requirement 2 in order to satisfy requirement 1.

## What we built instead

Generation runs on the **provider seam** (`app/providers.py`), the same
`ModelProvider` interface Phase 1 proved against both Ollama and DeepSeek with
zero application-code change. The agent layer (`app/agent.py`) is a real,
explicit boundary:

```
API -> session -> agent -> retrieval -> prompt -> provider -> grounding -> persistence
```

It preserves every property the agent layer is supposed to provide:

| Property | How |
|---|---|
| Provider switching by configuration only | `get_provider()`; no business logic branches on provider identity |
| Deterministic retrieval, not model-dispatched | `app/retrieval.py`, unchanged from Phase 3 |
| Tool surface minimal | There is no tool surface at all — nothing can call Bash or Write |
| Observability | provider, model, retrieval status, grounding verdict, latency all logged |
| Grounding enforced | `app/grounding.py` verifies **every** generated answer |

## Honest accounting

- **This is a deviation from a mandated item**, stated plainly rather than
  buried. We are not claiming Agent SDK compliance.
- Phase 1 *did* demonstrate the SDK working against both providers (T1/T2/T3),
  including custom tool calling. That evidence stands and is in the plan; what
  Phase 4 adds is that it cannot coexist with the locked local context budget.
- **The SDK remains viable on the cloud path.** DeepSeek has a large context
  and no VRAM constraint. We deliberately did **not** wire it there, because
  running the agent layer through one implementation on the cloud and another
  locally would break "switch provider by configuration only" — the §3.2
  property we consider more valuable than nominal §3.1 compliance.
- If an evaluator requires the SDK specifically, the cleanest path is to run it
  on DeepSeek only, accept two execution backends, and document that the local
  and cloud paths differ. We judged that worse. That judgement is the reviewable
  decision here.

## Reproducing

```bash
# with Claude Code CLI available and OLLAMA_CONTEXT_LENGTH=8192
python - <<'EOF'
from claude_agent_sdk import query, ClaudeAgentOptions
# ... allowed_tools=[], setting_sources=[] -> still ~24.5K tokens
EOF
```

The failing call and its three variants are recorded above with exact token
counts.
