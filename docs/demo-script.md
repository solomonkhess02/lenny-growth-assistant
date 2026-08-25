# Demo video script

**Requirement:** 2–3 minutes, **camera enabled**. Explain the problem, show the product,
demonstrate local Ollama, and briefly cover one important technical trade-off. Upload to YouTube.

---

## The constraint that shapes everything

**A local Ship 30 essay takes 254.7 seconds. A local answer takes ~28 seconds.**

The total budget is 150–180 seconds. So real-time generation of an essay is impossible, and even
one live answer would consume a fifth of the video watching a caret blink.

**The solution: generate everything before recording, then show it from history.** Replayed turns
render identically to live ones — citations, verdicts, retractions and all — because sources and
grounding are persisted. Nothing is faked; you are showing real output you produced minutes
earlier.

Show *one* generation starting live (to prove it is real and streaming), then cut. Do not wait for
it on camera.

---

## Pre-flight checklist

Run through this **before** hitting record. Most of it is about not showing an evaluator a
cluttered screen.

- [ ] **Clean the session list.** A sidebar full of `transportfix deepseek growthteam r3` looks
      like a lab bench, not a product. Either start from a fresh database volume, or delete the
      experiment sessions:
      `curl -X DELETE http://localhost:8000/api/sessions/<id>` for each.
- [ ] **Pre-generate the demo content**, in this order, in one session:
      1. An answerable question → verified answer with citations.
      2. A Ship 30 essay from it (~4 minutes — start it and go make coffee).
      3. In a *second* session, an unsupported question → abstention.
- [ ] **Warm the model** so the live shot's first token is fast:
      `curl http://127.0.0.1:11434/api/generate -d '{"model":"qwen3:4b-instruct","prompt":"hi"}'`
- [ ] **Check `ollama ps`** shows the model resident — this is your proof shot.
- [ ] **Browser at 1440×900 or wider.** Below 1100px the artifact pane is hidden by design and the
      whole Artifact Viewer demo disappears.
- [ ] **Hide bookmarks, close other tabs**, silence notifications.
- [ ] Have a terminal ready in a second window for `ollama ps` and `docker compose ps`.

> **If the essay you pre-generate is retracted** (likely on the local model — 0 of 12 passed at
> n=3): **use it**. See the note at the end. It is the strongest 20 seconds available to you.

---

## Shot list

Total 2:30, leaving ~30 s of slack.

### 0:00 – 0:22 · The problem *(camera on you)*

> "A growth team wants to use Lenny's Podcast as an internal knowledge base. The problem isn't that
> the transcripts are long — it's that advice you can't attribute is advice you can't use. If I
> tell my team 'Duolingo did something clever with streaks,' that's not something anyone can act
> on. And a general chatbot makes this *worse*: it gives you fluent, confident, unverifiable
> claims, in exactly the same voice whether it's right or wrong.
>
> So I built an assistant that answers only from the transcripts, cites the episode and timestamp
> behind every claim — and retracts its own answer when it can't back it up."

*Keep this tight. It is the only part with no visuals, and it sets up everything after it.*

### 0:22 – 0:50 · A grounded answer *(screen, camera inset)*

1. Type: **"How does Duolingo use streaks to improve retention?"** and hit Send.
2. **Point at the citations the moment they appear, before any text.**

> "Notice the sources arrive *before* the answer does. That's deliberate — citations are evidence
> the system retrieved, not claims the model made, so they're trustworthy before a single token
> exists."

3. Let it stream for ~4 seconds. **Cut** to the completed version from history.
4. Point at the green verdict.

> "And after the text: every quote and every citation tag has been checked against the retrieved
> evidence. Two quotes checked, both real."

### 0:50 – 1:08 · The refusal

Switch to the pre-made session, ask something outside the corpus — **"How do I make a sourdough
starter?"**

It returns in about a tenth of a second, because retrieval finds nothing and **the model is never
invoked at all.**

> "No evidence, no answer — and that's structural, not a prompt. If retrieval comes back empty the
> model never runs, so abstention isn't something it can decide to skip. It's also styled as a
> normal state, not an error, because the system declining is the system working."

### 1:08 – 1:40 · The Ship 30 essay and the Artifact Viewer

1. Click **Write a Ship 30 essay** on the verified answer — let it start, show the elapsed clock
   ticking and the word count climbing. **Cut after ~5 seconds.**
2. Open the pre-generated essay from history.
3. Toggle **Formatted** / **Source**.

> "Essays are written only from an answer that already passed verification — a thousand confident
> words built on a fabrication would just make the mistake more shareable. It renders beside the
> chat, and the generated HTML is treated as untrusted: sanitized server-side, then isolated in a
> sandboxed iframe with no scripts and no same-origin. Two independent gates, because either one
> alone is a single bug away from failing."

### 1:40 – 2:00 · Local Ollama *(the mandated proof)*

Cut to the terminal beside the browser:

```bash
ollama ps          # qwen3:4b-instruct, resident
curl -s localhost:8000/api/config | jq '.llm_provider, .ollama_model'
```

Then point at the header pill: **`ollama · qwen3:4b-instruct`**.

> "Everything you've seen is running on a 4-billion-parameter model on my own GPU. No cloud call.
> The header always names the provider and model for the session you're in — and provider is fixed
> per session, so an answer can never be quietly produced by something other than what it says."

### 2:00 – 2:30 · One trade-off *(camera)*

Pick **one**. The framework decision is recommended — it is the assignment's own either/or, and the
answer is a number rather than an opinion.

> "The trade-off I'll mention: the assignment allows either the Claude Agent SDK or the Pi Coding
> Agent. The SDK is the more obvious choice — better documented, first-party. But my context window
> is locked at 8,192 tokens, because that's what fits in 4 GB of VRAM. I measured the SDK's
> harness — its system prompt and tool definitions — at **24,472 tokens.** Three times the entire
> budget, before any evidence or any question.
>
> So it wasn't a preference, it was arithmetic. I used Pi, and it cost me latency — a Node process
> per request. I took that cost knowingly, and it's written down. That's the pattern across the
> whole project: measure the thing, write down what it cost, don't argue with it."

---

## If your local essay is retracted — lead with it

Do not hide this. Swap in 15 seconds and it becomes the best moment in the video:

> "Here's something honest. On a 4B model, essays fabricate quotations — I measured it at zero out
> of twelve passing, about a 20% per-quote fabrication rate. Watch what happens: the system catches
> it, strikes the essay through, and names the exact quotes that appear nowhere in the evidence.
>
> I could have hidden that by loosening the checker. I measured a prompt fix that lowered the
> fabrication rate — and reverted it, because it didn't change a single verdict. This is the
> product working. It's supposed to refuse."

An evaluator has seen many demos where everything succeeds. Very few where the presenter shows a
failure, explains it with numbers, and demonstrates that the system caught it.

---

## Do not

- Do not wait on screen for a local generation. Cut.
- Do not narrate the architecture — it is in `architecture.md`. Show the product.
- Do not apologise for the local model's speed or essay quality. State the number and move on.
- Do not exceed 3:00.

## Upload

YouTube, **unlisted or public** — not private, or the evaluator cannot open it. Title:
*"The Lenny Growth Assistant — FDE take-home"*. Put the repository link in the description.
