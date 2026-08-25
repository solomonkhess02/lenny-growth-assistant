# Agent framework comparison: Claude Agent SDK vs Pi Coding Agent

**Phase 4.5 decision spike · 2026-08-25 · Status: ✅ ADOPTED — Pi is now the agent framework**

> **Outcome:** Pi Coding Agent was adopted. Generation for **both** providers runs through
> `backend/app/pi_runtime.py`. The provider seam (`get_provider()`) is unchanged, so provider
> selection remains configuration-only and `app/agent.py` required **no modification at all**.
> Adoption notes are in §13.

§3.1 permits either the Anthropic Claude Agent SDK **or** the Pi Coding Agent as the agent
layer. Phase 4 shipped on neither, because the Claude Agent SDK could not fit our locked
8,192-token local context. This spike asks one question: **can Pi?**

Everything below is measured on this machine against the real locked configuration —
`qwen3:4b-instruct`, `OLLAMA_CONTEXT_LENGTH=8192`, Ollama at `127.0.0.1`. Nothing was relaxed
to make a result fit.

---

## 1. Headline result

| | Claude Agent SDK | Pi Coding Agent |
|---|---|---|
| Minimum prompt overhead | **24,472 tokens** | **111 tokens** |
| Fits our 8,192 context? | ❌ **No** (299% of budget) | ✅ **Yes** (1.4%) |
| Realistic RAG request | ~24,662 — cannot run | **1,662** (20% of budget) |
| Runs locally at all | ❌ 400 before any generation | ✅ Yes |
| Tool dispatch on `qwen3:4b-instruct` | untestable locally | ✅ **Works** |
| Provider switch to our cloud provider | ✅ (Phase 1) | ✅ **Works** (corrected — see §7) |

**Pi has 220× less prompt overhead than the Claude Agent SDK** and leaves 6,530 tokens of our
8,192 budget free after a full three-chunk RAG prompt.

---

## 2. Context-token breakdown

Ground truth is `usage.input` reported by the model for each request — the server's own count,
surfaced through Pi's `--mode json` events. It is not an estimate. Component costs marked
*(differential)* are obtained by subtracting two measured configurations; where a component
could not be isolated cleanly that is stated rather than guessed.

### Pi — component costs

| Component | Tokens | How obtained |
|---|---|---|
| Harness floor (framework only) | **111** | Measured directly: clean dir, `--no-tools`, `--no-prompt-templates`, 3-word system prompt |
| Pi's default coding system prompt | **346** | *(differential)* 1,768 − 1,422 |
| All built-in tool schemas (8 tools) | **987** | *(differential)* 2,755 − 1,768 |
| One tool only (`read`) | **278** | *(differential)* 2,046 − 1,768 |
| Prompt templates | **0** | 1,422 with and without `--no-prompt-templates` |
| Our Phase 4 system prompt (858 chars) | **243** | *(differential)* within the clean RAG total |
| Retrieved evidence, 3 real chunks | **962** | *(differential)* 2,384 − 1,422 |
| User prompt ("Say BANANA.") | **~4** | Trivial baseline |
| Conversation history | **not isolated** | Single-turn `-p` mode; multi-turn not measured. Pi emits `compaction_start` at a threshold, so history is managed, but the growth curve is unmeasured |

### Reconciliation — the parts add up

Clean-directory realistic RAG request, measured **1,662**:

```
  111  harness floor
+ 346  Pi default system prompt (--append-system-prompt keeps it)
+ 243  our Phase 4 grounding system prompt
+ 962  three retrieved evidence chunks
-----
 1,662  ✓ matches the measured total exactly
```

### 🔴 A trap worth knowing: Pi auto-injects project context

Measured **in our repository**, the same "floor" configuration cost **1,422 tokens**, not 111.
The 1,311-token difference is **our own `CLAUDE.md`**, which Pi discovers and appends as project
context. Confirmed in `dist/core/system-prompt.js`:

```js
if (contextFiles.length > 0) {
    prompt += "\n\n# Project Context\n\n";
    ...
}
```

If Pi were embedded in the backend and run from the repository root, **every RAG prompt would
silently carry 1,311 tokens of engineering instructions irrelevant to answering a podcast
question** — 16% of the context budget, invisible unless measured. Controllable via working
directory, but it must be controlled deliberately.

### Claude Agent SDK — for comparison

| Configuration | Tokens |
|---|---|
| Default (tools + settings + default prompt) | 24,561 |
| `allowed_tools=[]`, `setting_sources=[]` | **24,472** |
| Above + replacement `system_prompt` | **24,663 — went UP** |
| Realistic RAG prompt | 24,662 |

Two findings. First, the overhead is **irreducible**: stripping every tool and setting moves it
by 0.4%. Second, `system_prompt` **appends** rather than replaces — supplying our own prompt made
the request *larger*. Pi's `--system-prompt` genuinely replaces (its `--append-system-prompt`
appends), which is the structural reason Pi can be made small and the SDK cannot.

Component isolation was not possible for the SDK: it errors at 400 before returning usage, so
these totals come from the server's `n_prompt_tokens` in the rejection. That is real, but it is a
single number, not a decomposition.

---

## 3. Compatibility with the locked Ollama setup

| Requirement | Pi | Evidence |
|---|---|---|
| `qwen3:4b-instruct` | ✅ | `models.json` provider, `openai-completions`, `127.0.0.1:11434/v1` |
| 8,192 context | ✅ | RAG request 1,662 tokens; 6,530 headroom |
| Ollama officially supported | ✅ | `docs.ollama.com/integrations/pi`; also `ollama launch pi` |
| IPv4 transport lock respected | ✅ | `baseUrl` set to `127.0.0.1`, never `localhost` |
| Declares context explicitly | ✅ | `contextWindow: 8192` in config — the constraint is visible in the config |

## 4. Tool and skill behaviour

**Tool dispatch works on our 4B local model.** Given `--tools read` and asked to read a file, Pi
dispatched the tool and returned the contents:

```
"type":"tool_execution_start"
"toolName":"read"
"text":"The secret codeword is PLATYPUS-42.\n"
```

This matters because it means Pi's agent capability is **real on our hardware**, not decorative.
The Claude Agent SDK's equivalent capability could not be exercised locally at all.

Tool control is granular: `--no-tools`, `--no-builtin-tools`, `--tools <allowlist>`,
`--exclude-tools`. For our architecture the correct setting is `--no-tools`: retrieval stays
deterministic application code (skill 03), and `read`/`write`/`bash` in a web backend are a live
security liability.

Skills: Pi loads a skills section only when the `read` tool is available, so `--no-tools`
disables skills too. Not exercised further — with retrieval outside the agent we have no use for
them in Phase 4/5.

## 5. Streaming

✅ Works. `--mode json` emits `text_start` / `text_delta` / `text_end` events with incremental
deltas, plus `tool_execution_start|update|end`, `turn_end`, `agent_end`. This maps cleanly onto
our existing SSE protocol (`sources → delta* → grounding → done`), so streaming — a locked
baseline requirement — would survive adoption.

## 6. Latency

| Path | Realistic RAG answer |
|---|---|
| **Phase 4 provider seam** (current, in-process) | **24.2 s** |
| Pi, `-p` subprocess per call | **52 s / 42 s** (two runs, ~47 s) |
| DeepSeek via provider seam | 3.9 s |

Pi roughly **doubles local latency**. Much of that is Node process startup per invocation;
Pi's `--mode rpc` or the TypeScript SDK would hold one process open and amortise it, so ~47 s is
an upper bound rather than the floor. **That was not measured** — claiming the amortised figure
without testing it would be exactly the kind of unverified assertion this project forbids.

## 7. Provider switching — WORKS (this section is a correction)

> ⚠️ **An earlier version of this document reported that Pi could not authenticate with
> DeepSeek, across five configurations. That finding was WRONG, and the fault was mine, not
> Pi's.** It is left described here rather than quietly deleted, because the cause is
> instructive.

**What actually happened.** My shell one-liner extracted the key from `.env` by taking
everything after the first `=`. That line carries an inline comment:

```
DEEPSEEK_API_KEY=<35 chars>     # required for LLM_PROVIDER=deepseek
```

So I was passing a **76-character** string — key + whitespace + comment — where the real key is
**35 characters**. `python-dotenv` (which the application uses) strips inline comments; my
parser did not. Pi was handed a malformed credential every single time and correctly rejected
it.

**What misled me further.** DeepSeek's error masks the credential as `****seek`, showing only its
last four characters. The 76-character string I was sending ended with the comment text
`...LLM_PROVIDER=deepseek` — so the mask displayed `seek`, and the message looked exactly as if
Pi were sending the literal provider name `"deepseek"`. I built a confident, wrong theory on
that coincidence.

*(Corrected again 2026-08-25 during Docker verification: an earlier revision of this section
said "our real key happens to end in `seek`". It does not. The **malformed** string did, because
of the trailing comment. Same root cause — my parsing bug — but the detail was wrong and is
fixed here rather than left to mislead the next reader.)*

The control that would have caught all of this immediately — comparing my extracted key against
the one the application loads — is the one I ran last instead of first.

**Verified correct behaviour**, with the key extracted the same way the app does it:

| Test | Result |
|---|---|
| `DEEPSEEK_API_KEY` env var, built-in `deepseek` provider, no `/login` | ✅ generates |
| `pi --list-models deepseek` | ✅ lists `deepseek-v4-pro`, `deepseek-v4-flash` |
| Ollama → DeepSeek, same command, only `--provider/--model` changed | ✅ both answer (7 s / 4 s) |
| Real key written to `models.json` or `auth.json` | ❌ **never** — both verified clean |

**Exact mechanism.** `pi-ai/dist/env-api-keys.js` holds a fixed map from provider name to env
var — `deepseek: "DEEPSEEK_API_KEY"`, `openai: "OPENAI_API_KEY"`, and so on. Two consequences:

1. The provider must be named **exactly** `deepseek` to pick up the env var. A *custom*
   `models.json` provider with the same name **shadows the built-in one** and breaks credential
   resolution; a custom provider under a different name (`ds-anthropic`) has no env mapping at
   all. Both of my earlier configurations were self-inflicted.
2. For DeepSeek, **no `models.json` entry is needed at all.** Delete it and set the env var.

## 8. Failure handling — a real defect for programmatic use

| Failure | Message | Exit code |
|---|---|---|
| Unreachable endpoint | `Connection error.` (after a long hang) | **0** |
| Nonexistent model | `Warning: Model "..." not found` + `404 model ... not found` | **0** |

Messages are legible, but **both failures exit 0**. A supervising process — our FastAPI backend,
or a container healthcheck — would read success. Our own CLI (`python -m app.ingest`) was
deliberately built to exit non-zero on failure for exactly this reason. Any adoption of Pi would
need a wrapper that parses the JSON event stream for `error` events rather than trusting the exit
status.

The unreachable-endpoint case also did not fail fast, which conflicts with our "no hangs"
resilience requirement.

## 9. Advantages and disadvantages

### Pi — advantages
- **Fits the locked context** with 80% of the budget to spare. The decisive property.
- **220× less overhead** than the Claude Agent SDK.
- Real tool dispatch on a 4B local model.
- Genuine system-prompt replacement, granular tool control, native streaming.
- Officially supported by Ollama; open source and inspectable — the 1,311-token project-context
  behaviour was found by reading its source.
- Would give **genuine §3.1 compliance**.

### Pi — disadvantages
- **~2× local latency** as invoked; amortisable but unproven.
- **Failures exit 0** — unsafe for programmatic supervision without a wrapper.
- **TypeScript/npm only**; our backend is Python, so integration is a Node subprocess, adding a
  runtime to the Docker image and a process boundary to every request.
- Silent project-context injection is a footgun.
- 110 npm packages added to the deployment surface.

### Claude Agent SDK
- **Advantage:** the most literal reading of §3.1; Phase 1 proved text, tools, and skills work
  on both providers when context is not a constraint.
- **Disqualifying disadvantage:** cannot run at all against the mandated local demo path.

---

## 10. Assignment compliance

| Requirement | Phase 4 (current) | Pi | Claude Agent SDK |
|---|---|---|---|
| §3.1 agent layer is a named framework | ❌ | ✅ | ✅ on paper, ❌ in practice |
| §3.1 demo runs on Ollama | ✅ | ✅ | ❌ |
| §3.2 switch provider by config | ✅ | ✅ | ✅ |
| Streaming baseline | ✅ | ✅ | untested locally |
| No hangs / failures surfaced | ✅ | ⚠️ exits 0 | n/a |
| Grounding enforced on output | ✅ | unchanged — sits outside the framework | unchanged |

The honest summary: **Pi trades a §3.2 regression and an operational defect for §3.1
compliance.** Neither framework gives us everything.

## 11. Is Pi genuine or ceremonial compliance?

I said in the plan I would answer this plainly.

For **our** architecture, adopting Pi with `--no-tools` would be **largely ceremonial**.
Retrieval is deterministic application code and stays outside the agent by design (skill 03, and
a ~24 s local tool round trip). With tools disabled, Pi would be a subprocess that takes a system
prompt plus an evidence block and returns text — exactly what `providers.py` already does
in-process, in half the time, with working provider switching.

What Pi would genuinely add is a **named framework in the stack** and a real, demonstrated tool
loop we currently do not use. What it would genuinely cost is cloud provider switching, 2× local
latency, a Node runtime in the image, and a failure mode that exits 0.

That is a real trade, and it is the user's call, not mine to make silently.

---

## 12. Recommendation

**Do not adopt Pi as a drop-in replacement for the Phase 4 generation path. Do adopt it if
§3.1 compliance is judged non-negotiable — and if so, adopt it narrowly and fix the two defects
first.**

Reasoning, in priority order:

1. **Pi clears the gate the Claude Agent SDK failed.** If an agent framework is required, Pi is
   the only one of the two that can run on the mandated demo path. That question is now settled
   with evidence.
2. **§3.2 is NOT a blocker** (corrected 2026-08-25). Provider switching works: Ollama and
   DeepSeek both answer from the same command with only `--provider/--model` changed, with the
   cloud key supplied by environment variable and never written to disk.
3. **The failure-exits-0 behaviour is the one remaining hard defect**, and it is fixable at the
   wrapper level rather than requiring a Pi change — the JSON event stream carries a clean
   discriminator (`stopReason: "error"` plus `errorMessage`).
4. **What is left is a cost/benefit judgement, not a blocker**: ~2× local latency, a Node
   runtime in the image, and — for our architecture specifically — a framework that would be
   running with all tools disabled.

Concretely, I recommend:

- **Now:** keep the Phase 4 provider seam as the generation path. It is faster, switches
  providers, fails loudly, and is fully tested (202 passing).
- **Document honestly** that no mandated agent framework is used, and *why* — `agent-layer-
  decision.md` plus this comparison give an evaluator the measurements to judge for themselves.
  A documented, evidence-backed deviation is more defensible than a framework adopted for
  appearance that halves performance and breaks provider switching.
- **If you decide §3.1 must be satisfied literally:** both providers can go through Pi — the
  credential issue was mine, not Pi's. The required work is a subprocess wrapper that parses
  `--mode json`, maps `stopReason == "error"` to a raised `ProviderUnavailable`, forces the
  working directory away from the repo root (or the 1,311-token `CLAUDE.md` injection returns),
  and passes `--no-tools`. Roughly half a day, behind a config flag, with both paths tested.

**No application code was changed by this spike.** The 2-word quote rule and all 19 grounding
tests are untouched; the suite still reports **202 passed**.

---

## Reproducing

```bash
npm install -g @earendil-works/pi-coding-agent      # 0.74.2 tested
# ~/.pi/agent/models.json: ollama @ http://127.0.0.1:11434/v1,
#   api "openai-completions", contextWindow 8192

# floor — run from an EMPTY directory or CLAUDE.md is absorbed
pi -p --mode json --provider ollama --model qwen3:4b-instruct \
   --no-tools --no-prompt-templates --system-prompt "Answer briefly." "Say BANANA."
```

Raw evidence: `spike/pi_eval/` — `clean_dir_results.log`, `failure_results.log`,
`tool_dispatch_results.log`, `measure.py`, plus the real system prompt and evidence block used.


---

## 13. Adoption (2026-08-25)

Pi is the project's §3.1 agent framework. What shipped:

| Aspect | Decision |
|---|---|
| Boundary | `backend/app/pi_runtime.py` — subprocess, `--mode json`, streams `text_delta` |
| Seam | Unchanged. `ModelProvider.stream()` is now a **single inherited** implementation delegating to Pi; neither provider overrides it |
| Provider mapping | `OllamaProvider.pi_provider == "ollama"`, `DeepSeekProvider.pi_provider == "deepseek"` |
| Tools | `--no-tools`, always. Pi's read/write/edit/bash never reach the backend |
| Working directory | A dedicated dir outside the repo, or Pi injects our `CLAUDE.md` (+1,311 tokens) |
| Credentials | `DEEPSEEK_API_KEY` passed to the child process only; never in `models.json`, `auth.json`, argv, or logs |
| Failure semantics | `stopReason == "error"` → mapped into the existing `AppError` taxonomy and raised |
| Grounding | Unchanged and still mandatory — it runs *after* generation regardless of framework |

**Measured after adoption**, real grounded answers on the real corpus:

| Provider | Latency | Grounding |
|---|---|---|
| Ollama `qwen3:4b-instruct` | 36.4 s | PASS, 1 quote verified, 0 fabricated |
| DeepSeek `deepseek-v4-pro` | 19.8 s | PASS, 3 quotes verified, 0 fabricated |

**The latency cost is real and was accepted knowingly:** Ollama 24.2 s → 36.4 s, DeepSeek
3.9 s → 19.8 s. Both now pay Node process startup per request. `--mode rpc` would amortise it
and remains the obvious optimisation if the cost becomes a problem.

**A structural win worth noting:** because generation collapsed to one inherited method,
adoption *deleted* 133 lines of per-provider HTTP/SSE parsing. Config-only provider switching
is now true by construction — a provider cannot reimplement generation without a test failing.


### 13.1 A consequence worth knowing: configuration is now split

Adoption moved the **generation endpoint** out of application configuration.

| Concern | Where it lives now |
|---|---|
| Which provider/model to use | `.env` → `LLM_PROVIDER`, `OLLAMA_MODEL`, `DEEPSEEK_MODEL` |
| **The Ollama URL used for generation** | **`~/.pi/agent/models.json`** (Pi's config) |
| The Ollama URL used for *health checks* | `.env` → `OLLAMA_BASE_URL` |
| Cloud credential | `.env` → `DEEPSEEK_API_KEY`, forwarded to the child process |

`OLLAMA_BASE_URL` therefore no longer redirects generation — it governs the health probe only.
That separation is deliberate (a health check should not require the agent framework to be
installed or working), but it is a genuine split and it surprised a test, which is how it was
found. `tests/test_providers.py::test_unreachable_generation_backend_raises_retryable` and
`::test_health_check_still_honours_the_configured_url` now pin both halves.

**Deployment consequence:** ✅ **DONE (Phase 4.6).** `deploy/pi-models.json` is baked to
`/home/appuser/.pi/agent/models.json` and Pi is installed in the image. Verified by cold build
and in-container generation on both providers — see §14.

Required `models.json` (Ollama only — DeepSeek is a built-in Pi provider and must NOT be
redefined, or the custom entry shadows the built-in and breaks credential resolution):

```json
{"providers": {"ollama": {
  "baseUrl": "http://127.0.0.1:11434/v1",
  "api": "openai-completions",
  "apiKey": "ollama",
  "compat": {"supportsDeveloperRole": false, "supportsReasoningEffort": false},
  "models": [{"id": "qwen3:4b-instruct", "contextWindow": 8192, "maxTokens": 2048}]
}}}
```


---

## 14. Docker integration (Phase 4.6)

Adoption was originally verified on the host only. The container path is now verified too.

### How Pi gets into the image

The runtime is `python:3.13-slim`, which has no Node. Rather than `apt-get install nodejs npm`
(older Node, second toolchain in a Python image), a dedicated stage installs Pi under its own
prefix and the runtime copies **only** the Node binary plus that prefix:

```dockerfile
FROM node:22-slim AS piagent
RUN npm install -g --prefix /opt/pi @earendil-works/pi-coding-agent

# runtime:
RUN apt-get install -y --no-install-recommends libstdc++6   # the bare node binary needs it
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=piagent /opt/pi /opt/pi
ENV PATH="/opt/pi/bin:${PATH}"
```

`libstdc++6` was the one genuinely uncertain step at plan time and it *was* required.

### Verified in the container

| Check | Result |
|---|---|
| `node --version` | **v22.23.2** |
| `which pi` | **`/opt/pi/bin/pi`** |
| Container Pi version | **0.84.3** — host has 0.74.2, proving the container uses its own |
| Bind mounts on the api container | **none** — no host `~/.pi` leaks in |
| Ollama reachability | `host.docker.internal:11434`, health probe 50.7 ms |
| Grounded answer, Ollama | **30.3 s, PASS**, 2 sources, 0 fabricated |
| Grounded answer, DeepSeek | **15.7 s, PASS**, 5 quotes verified, 0 fabricated |
| `DEEPSEEK_API_KEY` length in container | **35** — Compose strips the `.env` inline comment |
| Key at rest anywhere in the image | **none** — filesystem grep of `/srv`, `/home/appuser`, `/opt/pi` |
| `auth.json` | `{}` — Pi never persisted a credential |
| Pi working directory | `/tmp/pi-workdir`, outside `/srv`, no project context files |

### Two defects the container verification caught

**1. The API container was running pytest.** The Dockerfile's last stage is `test`, and an
unpinned `docker compose build` resolves to the last stage — so the shipped API image had
pytest as its entrypoint instead of uvicorn. Fixed by pinning `target: runtime` on the api
service. This would not have been visible from the host at all.

**2. A test was passing on Windows for the wrong reason.**
`test_changed_transcript_leaves_no_stale_chunks` mutated its fixture with
`text.replace("retention", ...)` — but `casey-winters.md` contains **zero** occurrences of
"retention". The replace was a no-op. It passed on Windows only because `read_text`/`write_text`
translate `LF -> CRLF`, changing the bytes by accident; on Linux the file was byte-identical, the
transcript was correctly *skipped*, and the test's premise never held. Now appends a real turn in
binary mode.

Two further tests hard-coded host-specific URLs, and one derived the repository root as
`parents[2]` — which is `/` inside the image, making its assertion vacuous. All three now assert
deployment-independent invariants. That also closes the deferred Phase 2 finding **M5**
("tests assert 'defaults' while reading .env").
