# Artifact isolation — decision D-4

Phase 7. Closes [verification-matrix.md](verification-matrix.md) rows "Artifact Viewer —
rendering" and "Artifact isolation", and skill `06-artifact-security`'s permit/block/strip
requirement. Supersedes the Phase 6 holding position in `ArtifactPane.tsx`, which rendered every
essay as escaped Markdown source specifically *until* this document existed.

## 1. Trust model and boundaries

Per skill 06: the model, the conversation, and the transcripts are all untrusted authors. An
essay's Markdown is shaped by all three — a transcript could contain `<script>` verbatim, a
model can emit arbitrary markup whether prompted to or not, and neither the grounding verdict nor
the word-count check inspects markup safety, only factual support.

```
  model output (UNTRUSTED)
     -> ship30.py                stores bytes verbatim; makes no safety claim
     -> essays.markdown          untrusted text at rest, unchanged since Phase 6
     -> app/artifacts.py         THE boundary — parse + sanitize, server side, in Python
     -> GET .../render (JSON)    {html, blocked, stripped, policy_version}
     -> iframe srcdoc            isolation — opaque origin, no scripts, no same-origin
     -X  application document    untrusted markup NEVER crosses this line
```

Assets defended, in priority order:

1. **The app origin.** Session ids and the conversation live there; script execution in the app
   document is a total compromise.
2. **The reader's privacy.** An external `<img>`/`<link>` in generated text beacons the reader's
   IP and referrer to whatever host the model names — the most *likely* real incident, because a
   model emits plausible-looking URLs without being attacked at all.
3. **The trust chain.** A model-authored, clickable link is an unverified citation wearing a
   verified citation's clothes. Nothing in the corpus verifies a URL the model wrote; the only
   verified citations are the retrieval-derived ones `Citations.tsx` renders, outside this HTML.
4. **The page itself.** Unscoped CSS from a "complete HTML/CSS snippet" (skill 06's second
   required input class) can restyle or overlay the surrounding chat.

Out of scope, so it is not mistaken for an oversight: prompt injection that changes what the
essay *says* is a grounding problem, already owned by `verify_answer` ([grounding.py](../backend/app/grounding.py)).
This document governs how the essay is *displayed*, not what it claims.

## 2. Decision: sanitize AND isolate, not either

Skill 06 permits either strategy alone. This system uses both, because they fail differently: a
bug in the sanitizer is contained by the sandbox; a typo in the sandbox attributes is contained
by the sanitizer. Concretely:

- **Server-side sanitization** ([backend/app/artifacts.py](../backend/app/artifacts.py)) —
  `markdown-it-py` parses with `html=False`, so literal HTML in the Markdown *source* is escaped
  to inert text at parse time, before it is ever structure. `nh3` (the Rust `ammonia` sanitizer)
  then cleans whatever HTML the renderer legitimately produced, against an explicit allowlist.
  Two independent gates, not one.
- **Client-side isolation** ([ArtifactPane.tsx](../frontend/src/components/ArtifactPane.tsx)) —
  the sanitized HTML is injected via `srcdoc` into an `<iframe sandbox="">`, withholding both
  `allow-scripts` and `allow-same-origin`. The frame gets an opaque origin: no script execution,
  no access to app cookies/storage/DOM, no top-level navigation, no popups, and — via the frame's
  own `<meta>` CSP — no network requests of any kind.

Rendering happens only for a **complete** essay, never mid-stream: the live stream keeps the
Phase 6 escaped `<pre>` view, because half-parsed Markdown is exactly where parser bugs live. A
Formatted/Source toggle in the pane exposes both once an essay is done, defaulting to Formatted
when a safe render exists and to Source otherwise — the fail-closed target.

**A retracted essay (`grounding.grounded === false`) is never offered a formatted render at
all** — not merely defaulted away from it, the toggle itself is disabled. Formatting is a
credibility signal; withholding it from a known fabrication is the same rule that makes
`routers/essays.py` refuse to *write* an essay from a failed answer (409) in the first place.

Rendering happens on read, not on write: `essays.format` stays `"markdown"` and nothing new is
persisted. The render is a pure, deterministic function of `essays.markdown` plus the pinned
sanitizer version, so storing HTML would only create a second copy that a sanitizer fix could
fail to reach. **No migration in Phase 7.**

## 3. Isolation mechanics

```html
<iframe sandbox="" srcdoc={html} referrerPolicy="no-referrer" title="Rendered essay" />
```

`sandbox=""` is the load-bearing line: no value in the attribute means *no* permissions are
granted, specifically withholding `allow-scripts` and `allow-same-origin`.

The `srcdoc` document carries its own CSP via `<meta>`:

```
default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; form-action 'none'; base-uri 'none'
```

`default-src 'none'` is what makes the external-resource defence *total*: the frame cannot issue
a single network request, regardless of what the sanitizer let through. `style-src
'unsafe-inline'` admits only the typography block `buildSrcDoc()` authors itself — no attribute
survives sanitization on any tag (`artifacts.PERMITTED_TAGS` carries none), so there is no
untrusted CSS for an attacker to place there anyway.

The app document itself carries a second, independent CSP via response header
([main.py](../backend/app/main.py)):

```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
img-src 'self' data:; connect-src 'self'; frame-src 'self';
frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'
```

plus `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
`Cross-Origin-Opener-Policy: same-origin`. `frame-src 'self'` is what permits the `srcdoc` frame
to exist at all.

### Measured, not assumed: `srcdoc` inherits the embedder's CSP

First deploy shipped `style-src 'self'` with no `'unsafe-inline'` at the app level, on the
(reasonable-sounding) theory that the `srcdoc` document's own `<meta>` CSP would govern it
independently. **Verified false in-browser**, via a real console CSP violation caught by
Playwright: a `srcdoc` document does not merely consult its own policy — Chromium applies the
*embedding* document's CSP to it as well, and the more restrictive value wins per directive. With
`style-src 'self'` at the app level, the essay iframe's own typography `<style>` block was
silently blocked; the essay still rendered — safely — but in the unstyled browser default rather
than the intended theme.

The fix is a single, deliberately narrow loosening: `style-src 'self' 'unsafe-inline'` at the app
level. Inline **style** has no code-execution surface, unlike inline **script** — `script-src`
stays plain `'self'` throughout, and every other directive (`object-src 'none'`,
`frame-ancestors 'none'`, `default-src 'self'`) is unchanged. `tests/test_health_and_errors.py::test_security_headers_are_present_on_every_response`
asserts the exact string `style-src 'self' 'unsafe-inline'`, so a regression back to the broken
strict form fails a test rather than silently un-styling every essay again.

### Known gap: the dev server

These headers come from the FastAPI response, so they cover the built app at `:8000` (`docker
compose up`, the demo path) and **not** the Vite dev server at `:5173`, which injects its own
`<style>` tags during development. Stated here rather than papered over; it does not affect the
evaluator-facing path.

### Sizing consequence

With neither `allow-scripts` nor `allow-same-origin`, nothing — not the parent document, not the
frame itself — can measure the frame's content height (the usual `postMessage`-based
auto-resize trick needs script on both sides). The frame therefore fills the pane body
(`min-height: 60vh` in [styles.css](../frontend/src/styles.css)) and scrolls **inside itself**,
rather than the single-scroll-region layout Phase 5/6's M23/M24 measured. This is a direct,
load-bearing consequence of the sandbox attributes that make the frame safe — not an oversight —
and both checks were re-verified in-browser against the new geometry (§6).

## 4. Permit / block / strip policy

Expressed directly as the `nh3` allowlist in `app/artifacts.py`, so the code *is* the policy an
evaluator can read — not a separate description that can drift from it.

**Permitted** (no attributes on any tag — none survive sanitization, on any element):

```
h1 h2 h3 h4  p  ul ol li  strong em b i  blockquote  code pre  hr br
```

No `table`: the `commonmark` preset this module runs (deliberately — nothing beyond the core spec
is in scope) has no table extension enabled, so the parser can never emit one from the Markdown
path. Declaring it permitted would be a claim about a code path that does not exist.

**Blocked** — tag *and its entire content* removed (`nh3`'s `clean_content_tags`, not merely an
unwrapped tag with the text kept):

```
script style iframe object embed applet form input button link meta base svg math template noscript
```

plus every event-handler attribute (`on*`) and every HTML comment. On the Markdown path (the real
producer) this list is a second, independent gate: `html=False` already guarantees none of these
can exist as real tags at all — a `<script>` in the source is escaped to literal text (visible,
inert, never executed) before `nh3` runs. On the `format="html"` path — accepted by
`artifacts.render()` and exhaustively tested per skill 06, but **fed by no producer today** — `nh3`
is the *only* gate, which is exactly why the full list is enforced there rather than assumed
unreachable.

**Stripped / neutralized** (content kept, capability removed):

- **Links** — a Markdown `[text](url)` is never allowed to become a real `<a href>`. The
  renderer's `link_open`/`link_close` rules are overridden to suppress the tag and append the URL
  as inert parenthetical text: `the source (https://example.com/article)`. The reader can see
  where a citation-shaped link points without it ever being clickable, and the only clickable
  citations in the pane stay the retrieval-derived ones in `Citations.tsx`.
- **Images** — a Markdown `![alt](url)` never becomes a real `<img src>`; the `image` render rule
  replaces it with a visible `[image removed]` marker. An essay has no legitimate image, and
  images are the canonical exfiltration beacon.
- Any URL-bearing attribute otherwise reaching `nh3` is filtered with `url_schemes=set()` — an
  empty allowlist, so `javascript:`, `data:`, `vbscript:`, `file:` cannot arise on anything that
  *does* survive.

**Preserved verbatim, and asserted by test:** `[E#]`-style citation tags and quotation marks.
Grounding reads the stored source, and a reader comparing the rendered essay against the verdict
must see the same tags and quotes — a sanitizer that silently dropped `[E#]` would break the
audit trail without ever looking unsafe. (`markdown-it-py` HTML-entity-encodes a literal `"` in
text content the same way the CommonMark reference renderer does; `&quot;` decodes to `"` for the
reader, so this is lossless, not a loss.)

**Belt-and-braces re-scan.** After `nh3.clean()`, `artifacts.render()` re-scans its own output for
`<script`, `<iframe`, `<style`, `<svg`, `srcdoc=`, a quoted `on\w+=` attribute, or a dangerous
scheme actually sitting in an `href`/`src`/`action`/`formaction` attribute value. Expected to
never match; if it does, that means the allowlist itself has a gap, and this is what fails the
render closed instead of shipping it anyway. Deliberately **not** a bare substring check for
`"javascript:"` — a neutralized link's inert parenthetical text (`click me
(javascript:alert(1))`) is *safe, correct, policy-compliant output*, and a naive substring match
would refuse it for containing a word.

## 5. Fail-closed behaviour

One rule: if it cannot be rendered safely, it is not rendered. The pane falls back to the
Phase 6 escaped-source view, names the reason, and the server logs the event.

| Trigger | Result |
|---|---|
| `nh3`/parser raises, or the sanitizer import is broken | `ArtifactRenderFailed` (500, `artifact_render_failed`) |
| Post-render re-scan matches (§4) | `ArtifactUnsafe` (500, `artifact_unsafe`) — never observed in testing; exists for the sanitizer-regression case |
| Source exceeds 256 KiB | `ArtifactTooLarge` (413, `artifact_too_large`) — a real essay is ~10 KiB |
| Unknown `format` | `ArtifactUnsupportedFormat` (422, `artifact_unsupported_format`) |
| Essay's grounding verdict is FAILED or missing | **Not an error** — `rendered: false, reason: "retracted" \| "ungrounded"`, a 200. Rendering is never attempted, the same way `essays.py` never writes an essay from a failed answer |
| Render request unreachable / network error | Client (`useEssay.ts`) keeps the source view and records `renderError`; no blank pane |

Each `AppError` subclass lives in [errors.py](../backend/app/errors.py), following the existing
taxonomy rule: a new failure mode gets a subclass, never an ad-hoc `HTTPException`. The registered
handler logs every one (`handled_app_error`) with its code, so "surfaced and logged" holds for
every path, not just the ones exercised in this document.

`GET /api/essays/{id}/render` returns **JSON**, never `text/html`. An HTML response would be
navigable at its own same-origin URL — the one response shape that could turn a sanitizer miss
into stored, reachable XSS rather than an inert JSON string a script-less client has to
deliberately inject into a sandboxed frame to render at all.

## 6. Verification

**Automated — `backend/tests/test_artifacts.py` (43 tests).** Skill 06's five required cases,
plus the attack classes behind them: benign Markdown; a benign HTML/CSS snippet; `<script>`
inline, via `<svg>`, and double-HTML-encoded; every event-handler-attribute vector; a
`javascript:`-scheme link (verified safe *and* non-refused: the neutralized text is not a live
construct); external `<img>`/`<link>`; every one of the 16 hard-blocked tags, individually,
including HTML5 void elements where "content removed" correctly means nothing was ever inside the
tag to begin with; HTML comments; malformed/truncated/empty input; the 256 KiB size cap; an
unsupported format; a simulated sanitizer fault; a simulated allowlist-bypass caught by the
post-render re-scan. Two properties run across the corpus rather than as one-off examples: no
output ever matches the live-markup pattern, and a realistic essay fixture (multiple `[E#]` tags,
multiple bold quoted spans, modeled on a real generated essay's structure) survives with every
tag and quote intact.

**Endpoint — `backend/tests/test_essays.py` (5 new tests).** `GET .../render` on a verified essay
returns sanitized HTML with `[E#]`/quotes intact; on a retracted essay returns `rendered: false,
reason: "retracted"` with no `html` key at all; on an unknown id returns the structured 404;
links/images are neutralized end-to-end through the real router, not just the sanitizer in
isolation.

**CSP header — `backend/tests/test_health_and_errors.py`.** Asserts the app-level CSP string
exactly, including the `style-src 'self' 'unsafe-inline'` decision from §3 by name — a regression
back to the broken strict form is a test failure, not a silent re-break.

**Manual, in-browser**, against the *built* image at `:8000` (`docker compose up -d --build api`),
driven with Playwright/Chromium (headless Chromium against the real Docker stack, same method as
the Phase 5 M9/M22–M24 baseline) — a fixture essay was seeded directly via `repository.create_essay`
containing an inline `<script>`, an `onerror` handler, a `javascript:`-scheme link, an external
image reference, a genuine citation, and a genuine block quote, alongside a second, retracted
essay:

- **M17.** DOM inspection of the *app* document: zero occurrences of the script payload as live
  markup. Exactly one `<iframe>`, `sandbox=""`, no `allow-scripts`, no `allow-same-origin`,
  `srcdoc` present. Reaching into the frame's own content document: no real `<script>` tag exists
  anywhere in it; the payload text and the `onerror=` string are present only as inert text; the
  external image is replaced by `[image removed]`; `[E1]` survives. No JS `alert()` fired
  (Playwright's dialog listener stayed silent). Network trace across the whole page load and
  interaction: **zero requests** to the fixture's external host. The pane's own removal count
  ("2 elements removed by the artifact policy") matched the two neutralized elements exactly.
  Toggling to Source shows the same payload as Phase 6 always did — plain, escaped text.
- **Retracted essay.** The Formatted toggle button is rendered `disabled`; zero `<iframe
  class="essay-frame">` elements exist in the DOM for it, confirming rendering is never attempted,
  not merely hidden behind a default.
- **M22.** Pane opens, collapses (`.artifact.collapsed` class toggles), and reopens correctly;
  history sidebar lists both fixture essays.
- **M23.** With 8 chat turns and a long, multi-section essay both on screen at 1440×900: composer
  bottom edge measured **888px** against a 900px viewport (within bounds), page `scrollHeight`
  **900** (no page-level scroll) — re-verified after the sandbox attribute change altered the
  pane's internal geometry (§3), not assumed unaffected.
- **M24.** Scrolling `.artifact-body` to its end moved `scrollTop` from 0 to 95 (its full
  scrollable range at this content length) while the chat panel's own scroll position and
  `window.scrollY` were both unchanged before and after — independent scroll regions hold with
  the new iframe layout.

**Frontend invariant, asserted against the source, not just verified live —
`TestFrontendNeverParsesUntrustedMarkup` in `test_artifacts.py` (4 tests).** Phase 6 asserted "0
`dangerouslySetInnerHTML`, 0 `iframe`, 0 `innerHTML`" as a manually-checked property; Phase 7
changes it on purpose (one `iframe` now exists), so these lock in the new, narrower invariant as
an automated regression gate rather than leaving it to memory: zero `dangerouslySetInnerHTML` /
`.innerHTML =` anywhere under `frontend/src`; exactly one `<iframe>`, and its own attribute list —
not the file, which also legitimately *describes* the policy in prose — contains `sandbox=""` and
neither `allow-scripts` nor `allow-same-origin`; the iframe's `srcDoc` traces to the sanitized
render response, never to `essay.markdown` (the raw source); the Source view still renders
`{essay.markdown}` as a plain React text child, Phase 6's guarantee, unchanged. These skip loudly
in the container image, which ships only the built `dist/` and not `frontend/src` — same pattern
as the pre-existing `Dockerfile`/`.dockerignore` packaging tests.

**Docker.** `docker compose --profile test build api-tests` then `run` (build first — `run` alone
serves a stale image, the documented trap): **379 passed, 6 skipped** — 2 the pre-existing
packaging tests (`Dockerfile`/`.dockerignore`, absent since Phase 4.6), 4 the new frontend-source
tests above (`frontend/src`, absent by the same packaging logic). Host suite: **385 passed, 0
skipped** (332 baseline + 43 new artifact tests + 5 new essay tests + 1 new CSP test + 4 new
frontend-invariant tests). `npm run build` (`tsc -b && vite build`) clean.

**Wheel gate, before any of the above was written.** `pip install --only-binary=:all:
markdown-it-py nh3` inside `python:3.13-slim` (the exact runtime base) resolved both from
prebuilt wheels — `markdown_it_py-4.2.0-py3-none-any.whl` (pure Python) and
`nh3-0.3.7-cp38-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl` (a Rust/`ammonia` extension)
— with exit code 0 and no compiler invoked. No Dockerfile or base-image change was needed.

## 7. What Phase 7 deliberately did not touch

Retrieval, grounding, providers, Pi, and Phase 6 essay generation are unchanged — this phase adds
a read path (`GET .../render`) and a display boundary, nothing upstream of `essays.markdown`. No
schema migration; `essays.format` still reads `"markdown"`. The streaming `TurnState` machine in
`useEssay.ts` is untouched; rendering is fetched only once an essay reaches a terminal state
(`done`/`retracted`) or is replayed from history. No new HTML-artifact producer exists —
`artifacts.render()` accepts `format="html"` and is tested against it (skill 06 requires the
capability to be verified), but no route accepts caller-supplied HTML; the UI ships the Markdown
path only, because nothing in the product generates the other kind of artifact.

## Known gaps carried forward

- **Dev-server CSP** (§3) is a stated, accepted gap — the demo path (`docker compose up`) is
  covered; `npm run dev` is not.
- **No frame-height auto-resize.** A deliberate non-goal: implementing it would need script
  communication between the frame and the parent, which is exactly what `sandbox=""` exists to
  prevent. The frame scrolls internally instead (§3).
- **`format="html"` has no producer.** Tested and policy-complete, but unused. If a future phase
  adds an HTML-artifact source, it inherits this same boundary rather than needing a new one.
