# Design

UI and UX decisions for The Lenny Growth Assistant, and the reasoning behind them.

This interface has one unusual job. Most chat products are designed to make an answer feel
credible. This one is designed to make credibility **checkable** — and, when a check fails, to take
the answer back. Nearly every decision below follows from that, including the ones that make the
product look less confident than it could.

---

## 1. Principles

**1. Trust is visible, not implied.**
Every generated answer carries its evidence above it and its verdict below it. There is no state in
which the interface shows text and stays silent about whether it was checked. "No badge" is never
how a verified answer looks.

**2. Failure is legible, and distinct from other failures.**
Three things that a lesser interface would collapse into one red box are kept separate:
the system broke (`error`), the system answered and the answer cannot be trusted (`retracted`), and
the system declined because the transcripts do not support the question (`abstained`). Only the
first is a fault. The third is the product working correctly and is deliberately styled as a calm,
neutral state — not an error.

**3. Nothing untrusted enters the application document.**
Generated HTML never becomes app DOM. The single `<iframe sandbox="">` is the boundary, and it is
the only exception to a rule the codebase otherwise holds absolutely: **zero**
`dangerouslySetInnerHTML`, **zero** `innerHTML`.

**4. Ordering carries meaning.**
Where something appears on screen is an argument about what it is. See §4.

**5. Latency is a design problem, not just an engineering one.**
A local essay takes minutes. An interface that shows a spinner for four minutes is
indistinguishable from one that has hung, so the essay view carries a live elapsed clock and a
streaming body.

---

## 2. Information architecture

Three panes, fixed responsibilities:

```
┌────────────────────────────────────────────────────────────────┐
│  header · active session's provider · model · health           │
├──────────┬───────────────────────────────┬─────────────────────┤
│ Sessions │  Chat                         │  Artifact Viewer    │
│          │                               │                     │
│ history  │  evidence  ← above the text   │  essay metadata     │
│ + new    │  answer                       │  evidence list      │
│ session  │  verdict   ← below the text   │  Formatted│Source   │
│          │                               │  body               │
│          │  ─────────────────────────    │                     │
│          │  composer (always reachable)  │                     │
└──────────┴───────────────────────────────┴─────────────────────┘
   220px            flexible               clamp(320px, 28%, 440px)
```

| Region | Owns | Deliberately does not own |
|---|---|---|
| **Header** | Which provider and model this session uses, and whether it is reachable | Any control that changes them — provider is immutable per session |
| **Sessions** | Switching and creating sessions; provider is chosen here | Anything about the current turn |
| **Chat** | The conversation and every trust signal attached to it | Rendering artifacts |
| **Artifact Viewer** | One essay: metadata, evidence, and the Formatted/Source toggle | Conversation state |

**Why the provider indicator lives in the header rather than beside each message.** Provider is a
property of the *session*, not of a turn, and putting it where session-level facts live makes the
immutability legible without a word of explanation. Each assistant turn still restates its own
provider and model, so a turn read in isolation — or replayed from history — stays self-describing.

**Component map** ([`frontend/src/`](frontend/src/)):

| File | Responsibility |
|---|---|
| `api.ts` | HTTP and the SSE reader — the only place the network is touched |
| `types.ts` | Wire types and the `TurnState` union |
| `useChat.ts` / `useEssay.ts` | The per-session state machines |
| `components/Message.tsx` | One turn: citations, body, verdict, errors |
| `components/Citations.tsx` | Evidence cards with deep links |
| `components/GroundingBanner.tsx` | The verdict — pass badge, or the retraction |
| `components/ArtifactPane.tsx` | The essay surface and the isolation boundary |
| `components/Composer.tsx` / `SessionList.tsx` / `NewSessionControl.tsx` | Input and navigation |

`useEssay` deliberately **reuses `TurnState`** rather than defining a parallel vocabulary. An essay
streams the same protocol, so it has the same states, and `retracted` means the same thing in both
places.

---

## 3. Key interaction states

The state machine is the design. It is declared once in
[`types.ts`](frontend/src/types.ts) and every visual decision hangs off it.

```
sending → sourced → streaming → verifying → done
                                          ↘ retracted
          (no evidence) ────────────────→ abstained
          (any failure) ────────────────→ error
```

| State | What the user sees | Why it looks that way |
|---|---|---|
| `sending` | Status line with a caret | Request in flight, nothing back yet |
| `sourced` | **Citations already on screen**, still no text | Evidence exists before the answer does, so it is shown as soon as it is true |
| `streaming` | Text arriving under the citations, blinking caret | |
| `verifying` | Full text, verdict pending | The one genuinely uncomfortable moment: complete text, unknown trustworthiness. It is *labelled* rather than hidden |
| `done` | Green `✓ Verified against sources · N quotes checked` | Quiet. A pass is the normal case and should not shout |
| `retracted` | Body struck through and dimmed, under a banner naming each fabricated quote and invalid tag | The strongest visual treatment in the product. See §4 |
| `abstained` | Neutral note explaining that no transcript material supported the question | **Not** styled as an error. Zero `.turn-error`, zero fail verdicts — verified in-browser (M13) |
| `error` | Error code, message, and a Retry button *only* when the failure is retryable | The code is shown because it is the thing worth quoting in a bug report |

**Interrupted turns are their own case.** A turn that fails *after* text has streamed gets the
retracted treatment plus a distinct `.unverified-note` — "generation ended before this could be
checked against sources." It is not a retraction (nothing was checked and failed) and not a plain
error (there is real text on screen). Verified in a real browser: the partial body computed to
`opacity: 0.55` with `text-decoration-line: line-through`, and `verdictBadgeCount: 0` — no verdict,
pass or fail, was ever invented for it.

---

## 4. Design decisions, with reasons

### Citations render above the answer; the verdict renders below it

Not a layout preference — it mirrors what is *knowable when*. The `sources` SSE event precedes the
first token, because citations are evidence the system retrieved, not claims the model made; they
are trustworthy before any text exists, so they appear first. The `grounding` event necessarily
follows the text, because an answer cannot be checked before it exists.

The consequence is unavoidable and shapes the next decision: **the reader always sees the text
before the verdict.**

### A failed verdict is a retraction, not a footnote

Since the reader has already read it, appending a caution would leave a fabricated quote on screen
looking like an answer with a caveat. Instead the answer is **withdrawn**: struck through, dimmed,
under a banner that says it is not supported by the transcripts and *names the specific quotes that
appear nowhere in the evidence*.

Naming them matters. "This answer may contain inaccuracies" teaches a reader to ignore the warning.
"This quote appears nowhere in the evidence: *'golden goose'*" is a checkable claim about a
specific sentence.

### A retracted essay cannot be viewed Formatted

The Artifact Viewer's Formatted/Source toggle is **`disabled`** for a retracted essay — not
defaulted away from, not discouraged. Rendering a known fabrication with polished typography would
make it more shareable, and the effort required to do so should not be one click. This mirrors the
server's refusal to *write* an essay from an unverified answer: the same rule, enforced at both
ends.

### The Source view is the default, and the fallback for everything

`Source` — escaped text in a `<pre>` — is what shows while an essay streams, when rendering is
refused, and when the render endpoint errors. Half-parsed Markdown is exactly where parser bugs
live, so nothing is ever rendered rich before it is complete. Failing closed to escaped text means
a rendering fault degrades to *safe and ugly*, never to *unsafe*.

### Retry does not change provider

The retry button reissues on the same session, and therefore the same provider, and the hint says
so: *"Nothing is switched for you."* A retry that silently succeeded on a different model would
make the provenance stamped on the answer false.

### The composer is always reachable

Measured, not assumed: at 1440×900 with 8 chat turns and a long multi-section essay, the composer's
bottom edge sits at **888 px** inside a 900 px viewport and the page `scrollHeight` is **900** — no
page scroll at all. Chat and artifact scroll independently in their own regions (artifact
`scrollTop` 0 → 95 while chat scroll and `window.scrollY` stayed unchanged). Re-verified after the
sandboxed iframe changed the pane's internal scroll geometry, because nothing inside a
`sandbox=""` frame can auto-size itself.

### Visual language

Dark by default with a light-mode override via `prefers-color-scheme`; a small token set
(`--bg`, `--panel`, `--line`, `--fg`, `--dim`, `--accent`, `--ok`, `--warn`, `--bad`) and the
system font stack. Deliberately plain: the interface's job is to make provenance obvious, and a
distinctive visual identity would compete with that. Colour is never the *only* signal — the
retraction pairs red with strike-through, a heading, and named quotes.

---

## 5. Responsive behaviour

**Stated plainly, because it is a real limitation rather than a feature.**

| Width | Layout |
|---|---|
| ≥ 1100 px | Full three-pane: sessions · chat · artifact |
| < 1100 px | **Sidebar and Artifact Viewer are `display: none`.** Chat occupies the full width |

**This is desktop-first by decision, and the decision is arguable.** The Artifact Viewer is a
side-by-side reading surface — its entire purpose is showing an essay *beside* the conversation it
came from. Stacked into a narrow column it stops being that and becomes a second scroll region
competing with the chat. Rather than ship a degraded version, it is hidden.

**The honest consequence: on a phone or a narrow window you cannot reach the artifact viewer or the
session list at all.** For the internal-desktop-tool audience in [PRD.md](PRD.md) that is an
acceptable trade; for a broader audience it would not be, and the fix is a real piece of design
work — a drawer or a tab switcher with its own interaction model — not a media query. It was left
undone rather than done badly, and it is recorded here rather than omitted.

Within the desktop range the layout is fluid: the artifact pane is
`clamp(320px, 28%, 440px)` and collapses to a rail on demand, with the chat column absorbing the
difference.

---

## 6. Accessibility

**The measured baseline, stated as measured — not as an aspiration.**

**What is implemented:**

- **Live regions on exactly the states that matter.** Six `role="alert"` / `role="dialog"`
  landmarks: the retraction banner, the unverified-generation note (chat and artifact), the two
  error surfaces, and the new-session picker. These are the moments where something changes on
  screen that a sighted user would notice immediately and a screen-reader user would otherwise
  miss — a verdict arriving after the text, or an answer being withdrawn.
- **Semantic landmarks** — `<header>`, `<main>`, `<nav>` and `<section>` rather than a tree of
  `<div>`s.
- **Native controls throughout.** Buttons are `<button>`, the composer is a `<textarea>`; keyboard
  activation and focus behaviour come from the platform rather than from re-implementation. The
  Formatted toggle uses the native `disabled` attribute, so its unavailability is exposed to
  assistive technology rather than only being visual.
- **Colour is never the sole signal** (see §4).
- **Respects `prefers-color-scheme`.**

**What is not done, and would be next:**

| Gap | Consequence |
|---|---|
| No focus management on stream completion | A screen-reader user is told the verdict via the live region, but focus is not moved to it |
| Contrast ratios not audited against WCAG AA | The dim token `--dim: #98a2b3` on `--bg: #0f1115` is likely to pass; `--warn` on panel is untested. **Untested is untested** |
| No keyboard-only traversal audit | Native controls make this likely to work; likely is not verified |
| No skip link | With three panes, tabbing to the composer crosses the session list |
| Reduced-motion not handled | Only the streaming caret animates, so the exposure is small |

There are 2 `aria-label`s and 1 `aria-expanded` in the codebase — a small number, and appropriately
so: most elements here are natively labelled, and adding redundant ARIA to native controls makes
screen-reader output worse, not better. The gap is not missing attributes; it is the **absence of
an audit**, and claiming a level of accessibility that has not been tested would be exactly the
kind of unverified assertion this product exists to refuse.

---

## 7. What I would change next

1. **Make the artifact viewer reachable below 1100 px** — as a drawer with its own interaction
   model, not a media query.
2. **Run a real accessibility audit** — contrast, keyboard traversal, focus management — and record
   the results the way every other claim in this repo is recorded.
3. **Wire the existing `stop()`** into a visible Stop control. Both hooks already return it; no
   component binds it, so a long generation can only be abandoned by leaving the page.
4. **Show retrieval similarity more meaningfully** than a raw score — the citation cards currently
   expose `0.72`, which is precise and not yet interpretable.
