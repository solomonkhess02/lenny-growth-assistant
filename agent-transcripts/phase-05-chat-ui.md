# Phase 5 — The chat UI

**Commits:** `5f4ae80 feat(phase-5): chat UI with citations, retraction, and per-session providers` ·
`5b5f650 fix(ui): pin the composer and give the artifact pane its own scroll region`

The phase where the product's trust semantics stopped being backend properties and became things a
reader can see.

---

## The decision that shaped everything: a failed verdict is a retraction

Verification cannot precede the text it verifies. The `grounding` event necessarily arrives after
the answer has already been read.

The agent's first design appended a caution below the answer — the conventional pattern, and what
most AI products do.

**That was rejected**, and the reasoning is worth stating because it is the product's core
argument:

> A footnote leaves a fabricated quote on screen looking like an answer with a caveat.

"This response may contain inaccuracies" teaches a reader to ignore the warning within a week.
So instead the answer is **withdrawn**: struck through, dimmed, under a banner that names *the
specific quotes that appear nowhere in the evidence*. That is a checkable claim about a particular
sentence rather than a disclaimer about the category of software.

Three semantics were locked here and every later phase had to respect them:

1. **Provider is per session and immutable.** No PATCH endpoint exists; `MessageCreate` carries no
   provider field. Enforced by the absence of a route, not by discipline.
2. **No automatic substitution.** A dead provider ends the stream in a terminal error. The test
   asserts the *other* provider's name appears nowhere in the stream — an absence, checked.
3. **A failed verdict retracts.** Persisted (`messages.sources`, `messages.grounding`), so a
   reload shows the same retraction rather than a clean-looking answer.

---

## Correction 1 — the composer walked off the bottom of the screen

### The symptom

With a long conversation and a long artifact open, the composer — the only way to interact with the
product — scrolled out of view. Recovering it meant scrolling the whole page.

### The cause

The page grew with its content instead of the panes scrolling within a fixed viewport. Each region
had been built correctly in isolation and nobody had opened all three at once with real data in
them.

### The correction

`5b5f650`: a fixed-height app shell, with chat and artifact owning independent scroll regions.

Then it was **measured** rather than eyeballed, at 1440×900 with 8 chat turns and a long
multi-section essay:

| Measurement | Result |
|---|---|
| Composer bottom edge | **888 px** (inside the 900 px viewport) |
| Page `scrollHeight` | **900** — no page scroll at all |
| Artifact `scrollTop` moved 0 → 95 | Chat scroll and `window.scrollY` unchanged |

Re-verified again in Phase 7 after the sandboxed iframe changed the pane's internal geometry —
nothing inside a `sandbox=""` frame can auto-size itself, so a layout that depended on that would
have broken silently.

---

## Correction 2 — "verified structurally" was not verification

### What was claimed

At the end of the phase the UI was reported as verified. What that meant: the components existed,
the types were sound, `tsc -b` passed, and the backend contracts were tested.

None of that is evidence that a **human being looking at the screen** sees citations before text,
or that a retraction is visually distinguishable from a normal answer.

### The correction

Gap #16 was opened against the phase's own claim, and closed only after driving the real
application in a real browser against the real Docker stack:

- citations-before-text confirmed by DOM order
- retraction confirmed by **computed style**, not by class name
- abstention confirmed as a *non-error* state — 0 `.turn-error`, 0 `.verdict.fail`
- provider indicator and retry checked against a deliberately unreachable provider
- reload replay confirmed pixel-identical to the live view
- a citation followed to YouTube: correct episode, player at **6:58**,
  `video.currentTime === 418`, matching transcript line 96 `(00:06:58)`

That last one closed gap #2. Until then, the citation chain rested on construction — the code built
the URL correctly — rather than on anyone having clicked one.

**The browser driver deliberately lives outside the repository.** No test framework was added to
`frontend/package.json`; `tsc -b` remains the only frontend gate. The verification is recorded in
the matrix as manual steps M9–M24 with their measurements, rather than as a dependency.

---

## Why `retracted` and `abstained` are not `error`

The agent's first state model had one failure state. Collapsing them would have been simpler code
and a worse product, because a reader needs to distinguish three genuinely different situations:

| State | Meaning |
|---|---|
| `error` | The system broke |
| `retracted` | The system answered, and the answer cannot be trusted |
| `abstained` | The transcripts do not support the question — **the product working correctly** |

An abstention styled as an error trains the user to read a correct, careful refusal as a
malfunction. It is given its own neutral presentation, and M13 verifies in the DOM that it carries
no error styling at all.
