# Phase 7 — Artifact isolation

**Commit:** `19fba4e feat(phase-7): artifact isolation — sanitized, sandboxed essay rendering`
**Artifacts:** [`docs/artifact-isolation.md`](../docs/artifact-isolation.md)

---

## Correction 1 — a correct CSP that silently broke the feature

### What the agent did

Set a strict Content-Security-Policy on the application, including:

```
style-src 'self'
```

Textbook. Every hardening guide recommends exactly this, and it passes every review because it
*is* the stricter option.

### The symptom

The formatted essay rendered — and was completely unstyled. Not broken, not blank, not an error.
Plain black text on white inside the frame, as though the CSS had never been written.

No exception. Nothing in the server logs. Nothing in the test suite, which asserts on sanitized
HTML *content* and had no opinion about whether a browser would apply the styles.

### The cause

**A `srcdoc` iframe inherits the embedder's CSP.** The artifact frame is fed via `srcdoc`, so the
app's `style-src 'self'` applied to it — and the frame's own inline typography is, by definition,
inline. The browser blocked it.

Caught by a real console violation in a real browser. It could not have been caught by reasoning
about the policy in the abstract, because the policy is correct; what is non-obvious is which
document it governs.

### The correction

Loosen **`style-src` alone**, to `'self' 'unsafe-inline'`. `script-src` and every other directive
stayed maximally strict — which is the part that matters, since the threat is script execution, not
styling.

The reasoning is recorded in [`docs/artifact-isolation.md §3`](../docs/artifact-isolation.md) with
an explicit warning that **the failure mode is silent**: it appears as unstyled output, never as an
error, so anyone tightening these directives again will not be told they broke it.

---

## The decision: sanitize **and** isolate, not either

The agent initially proposed sanitization alone — `nh3` against an allowlist is genuinely good, and
the argument that it is sufficient is respectable.

It was rejected in favour of two independent gates:

| Gate | Mechanism | Contains |
|---|---|---|
| **Server-side** | `markdown-it-py` (`html=False`) then `nh3` against an explicit allowlist | Malicious markup, before it ever leaves the API |
| **Client-side** | `<iframe sandbox="">` — **no** `allow-scripts`, **no** `allow-same-origin` | Anything the sanitizer missed |

The point is the redundancy: **a sanitizer bug is contained by the sandbox; a sandbox typo is
contained by the sanitizer.** Neither is trusted to be sufficient alone, because both are one
mistake away from being insufficient and neither mistake is visible from the outside.

The application document keeps **zero** `dangerouslySetInnerHTML` and **zero** `innerHTML`. The one
iframe is the isolation boundary, not an exception to that rule.

---

## Verified against a real payload, not a hypothetical

A fixture essay was built carrying an inline `<script>`, an `onerror` handler, a `javascript:` link
and an external image, and rendered against the **built image at `:8000`** — not the Vite dev
server, because the CSP is set by the FastAPI response and only covers the built path.

Measured in the browser (M17):

| Check | Result |
|---|---|
| Live-markup occurrences in the app document | **0** |
| `iframe` count / `sandbox` value | exactly 1, `sandbox=""` |
| Real `<script>` tag inside the frame | none — payload present only as inert text |
| External image | replaced by `[image removed]` |
| `[E1]` citation | intact |
| Network requests to the fixture's external host | **0** |
| JS `alert()` fired | **0** |

Zero network requests is the one that matters most: it proves the isolation holds against
*exfiltration*, not merely against script execution.

---

## A rule that could have been a default, and was not

A retracted essay is confined to escaped source text. The natural implementation is to *default*
the view to Source for a retracted essay.

Instead, **the Formatted toggle is `disabled`**.

The difference is small in code and large in intent. Defaulting means rendering a known fabrication
with polished typography is one click away; disabling means it is unreachable. Since the entire
argument for retraction is that a fabrication must not be made *more* shareable, leaving the polish
one click away would have contradicted it.

It also carries an accessibility benefit that was not the original motivation: the native
`disabled` attribute exposes the unavailability to assistive technology, rather than communicating
it only visually.

---

## An accepted, documented gap

**The CSP does not apply to the Vite dev server.** Headers come from the FastAPI response, so they
cover the built app at `:8000` — the `docker compose up` and demo path — and not `npm run dev` on
`:5173`, which injects its own `<style>` tags.

Accepted rather than fixed: the evaluator-facing path is unaffected, and adding a parallel header
mechanism to a dev server would mean maintaining a second security configuration whose drift from
the real one would be invisible. Recorded as gap #19.
