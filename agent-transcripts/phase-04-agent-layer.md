# Phase 4 — The agent layer on Pi

**Commit:** `5fd09db feat(phase-4): agent layer on the Pi Coding Agent, verified in Docker`
**Artifacts:** [`docs/agent-framework-comparison.md`](../docs/agent-framework-comparison.md)

Four undocumented behaviours, each found by something failing in a way that did not look like its
cause.

---

## Correction 1 — Pi exits 0 on failure

### The symptom

Integration tests passed against a **deliberately invalid** API key. A bad model id also
"succeeded". The provider health story looked perfect and was meaningless.

### The cause

```
$ pi ... --provider deepseek     # with a rejected credential
$ echo $?
0
```

Unreachable endpoints, malformed model ids and rejected credentials all return exit status **0**.
The agent's error handling was built on `returncode != 0`, which is the correct instinct for every
other CLI and is simply wrong here.

### The correction

The only reliable signal is in the event stream:

```python
message.stopReason == "error"
```

Error detection moved onto the parsed JSON-lines events. Regression coverage in
[`tests/test_pi_runtime.py`](../backend/tests/test_pi_runtime.py), including recorded fixtures for
the bad-key and bad-model cases (`spike/pi_eval/ev_badkey.jsonl`, `ev_badmodel.jsonl`) so the
behaviour is pinned rather than trusted.

**The general failure:** a test that cannot fail is worse than no test, because it reports safety.
This one had to be proven able to fail before it was believed.

---

## Correction 2 — `--skill` silently delivers nothing under `--no-tools`

### What the agent did

The Ship 30 writing rules live in a skill file. Pi has a `--skill` flag. The obvious wiring:

```
pi --skill 05-ship30-writing --no-tools ...
```

### What was wrong

Essays came back generic — correct topic, no house style. The skill was named in the system prompt
but its content clearly never arrived.

Pi skills use **progressive disclosure**: only the name and description go into the system prompt.
The body is fetched by the agent calling the `read` tool.

`--no-tools` removes the `read` tool. So the body could never be delivered. The flag was accepted,
reported no error, and did nothing.

### The correction

Pass the file directly:

- `--system-prompt` → `app/prompts/ship30_rules.md` (the grounding rules)
- `--append-system-prompt` → `SKILL.md` (the craft)

This split turned out to matter beyond the bug: **trust rules are code-owned, craft is
skill-owned.** Editing the skill changes how an essay reads and can never relax grounding.

### A trap inside the fix

Pi's prompt flags read a file when the argument is a path — **and silently use the argument as
literal prompt text when the file does not exist.** A typo'd path becomes a prompt saying
`/app/skills/05-ship30-writing/SKILL.md`.

So `ship30.py` verifies existence before spawning, and searches the image path then the host path
so one authored copy serves both.

---

## Correction 3 — a custom `deepseek` entry broke credential resolution

### What the agent did

Added a `deepseek` provider entry to Pi's `models.json`, symmetrically with the Ollama entry.
Reasonable-looking configuration.

### What was wrong

DeepSeek is a **built-in** Pi provider that resolves `DEEPSEEK_API_KEY` by exact name. A custom
entry with the same name **shadows** the built-in, and the shadowing entry does not carry the
built-in's credential resolution. Authentication broke — and, per Correction 1, broke while
exiting 0.

### The correction

**DeepSeek is deliberately absent** from [`deploy/pi-models.json`](../deploy/pi-models.json), with
a comment explaining that its absence is load-bearing. The file stays credential-free by
construction; keys reach the child only through its environment.

---

## Correction 4 — Pi read `CLAUDE.md` into every prompt

### The symptom

Local answers degraded after the agent layer landed. Same model, same evidence, same context
length — worse output, and occasional truncation.

### The cause

Pi injects project context files (`CLAUDE.md`, `AGENTS.md`) discovered from its working directory.
The working directory was the repo root. So **this project's own instruction file was being
prepended to every user question.**

Measured: **+1,311 tokens per request** — 16% of the 8,192-token budget, spent on instructions
about how to write code, on every question about podcast transcripts. Invisible unless you count
tokens, because nothing errors; the model simply has less room for evidence.

### The correction

```yaml
PI_WORKING_DIR: /tmp/pi-workdir     # must stay OUTSIDE the repo
```

Pinned in Compose, documented in [`CLAUDE.md`](../CLAUDE.md), and given its own directory in the
image.

---

## What these four share

Every one of them **failed silently or misleadingly**: exit 0 on failure, a flag that accepted an
argument and ignored it, a config entry that shadowed rather than errored, and a context injection
that only showed up as slightly worse answers.

None would have been caught by code review. All four were caught by measuring something concrete —
an exit code against a known-bad key, the actual content of a prompt, a token count.

This is why `--no-tools` is now permanent: retrieval is deterministic application code, the agent
needs no tool surface, and read/write/edit/bash in a web backend is a liability rather than a
capability.
