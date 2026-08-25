# Phase 1 — Provider and local-model spike

**Commit:** `aee8c16 spike: Phase 1 provider and local-model validation harness`
**Artifacts:** [`spike/`](../spike/), [`docs/agent-framework-comparison.md`](../docs/agent-framework-comparison.md)

---

## What was asked

Before building anything: prove the two model paths work, and choose the agent framework. The
assignment mandates either the Anthropic Claude Agent SDK or the Pi Coding Agent, and Ollama for
the demo.

## What the agent proposed first

The Claude Agent SDK. It is the better-known option, it is first in the assignment's own list, and
the reasoning offered was entirely qualitative — better documented, more idiomatic, first-party.

That reasoning was never tested against the one number that mattered.

## What was wrong

**The context budget had already been locked, and nobody checked the framework against it.**

The local hardware is a 4 GB GTX 1650. Context length was fixed at 8,192 tokens by measurement:

| `OLLAMA_CONTEXT_LENGTH` | VRAM | Latency per call |
|---|---|---|
| 8,192 | 4.1 GB | ~33 s |
| 32,768 | 8.0 GB | ~173 s |

32,768 exceeds the card. So 8,192 is not a preference; it is the ceiling.

Measuring the Claude Agent SDK's harness — the system prompt and tool definitions it injects
before a single token of application content — gave **24,472 tokens**.

That is **three times the entire available context**, before any evidence, question or answer. The
SDK was not "heavier"; it was arithmetically impossible on the mandated demo hardware.

## The correction

Pi Coding Agent, chosen on the measurement rather than on preference. Recorded in
[`docs/agent-framework-comparison.md`](../docs/agent-framework-comparison.md).

The earlier decision document was **not deleted** — it carries a `⛔ SUPERSEDED` banner and is kept
as [`docs/agent-layer-decision.md`](../docs/agent-layer-decision.md), because a record of why an
option was rejected is worth more than a tidy history.

## Two other things the spike found

**DeepSeek returned an empty response.** Test C came back with nothing after 4,096 output tokens,
`stop_reason=max_tokens` — thinking content had consumed the whole budget (observed at 29,995
characters of it). This is why `DEEPSEEK_DISABLE_THINKING` and `DEEPSEEK_MAX_TOKENS` exist in
`.env.example`, and it resurfaced in Phase 6 as a much worse bug.

**The local essay ceiling was visible this early.** The spike measured a 2,220-output-token essay
task and flagged that a 4B model producing long-form grounded prose was the risky part of the
build. Phase 6 measured exactly that failure at n=3. The prediction did not prevent the problem,
but it did mean the eventual result was a confirmation rather than a surprise, and the plan
already had "report it" rather than "fix it late" as the response.

## Why this one mattered

Had this phase been skipped, the agent layer would have been built on the SDK and the failure
would have surfaced in Phase 4 as *"the local model gives nonsense answers"* — which looks like a
retrieval bug, or a prompt bug, or a bad model choice. It would have been debugged in the wrong
place, possibly for days.

The general lesson, which recurs in every phase after this one: **the agent's qualitative reasoning
was fluent and wrong, and only a number distinguished the two.**
