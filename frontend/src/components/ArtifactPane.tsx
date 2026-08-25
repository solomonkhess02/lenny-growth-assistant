/**
 * Artifact Viewer -- Ship 30 essays, isolated and sanitized (Phase 7).
 *
 * Decision D-4 (docs/artifact-isolation.md): sanitize server-side AND
 * isolate client-side, not either alone. `GET /api/essays/{id}/render`
 * (`backend/app/artifacts.py`) is the boundary that turns untrusted
 * Markdown into an allowlisted HTML string; this component's ONLY job is
 * to display that string without ever letting the app document parse it.
 *
 * The isolation mechanism is exactly one `<iframe>`, given `srcdoc` and:
 *
 *   sandbox=""            -- no allow-scripts, no allow-same-origin. The
 *                             frame lands in an opaque origin: no script
 *                             execution, no access to app cookies/storage/
 *                             DOM, no top-level navigation, no popups.
 *   referrerPolicy         -- belt-and-braces; the srcdoc document's own
 *                             CSP (`buildSrcDoc` below) already blocks every
 *                             network request the frame could make.
 *
 * There is still, deliberately:
 *
 *   - no dangerouslySetInnerHTML
 *   - no innerHTML
 *   - no Markdown-to-HTML step in this file, and no Markdown library in
 *     package.json -- parsing happens once, server-side, in artifacts.py
 *
 * `srcdoc` is a plain React prop here, not an escape hatch: React sets it
 * as the iframe's `srcdoc` attribute value, a string the app's OWN document
 * never parses. The string is only interpreted as HTML inside the sandboxed
 * frame's separate, script-less, storage-less browsing context -- which is
 * the isolation boundary, not a bypass of it.
 *
 * A retracted essay (`essay.state === "retracted"`) is never offered a
 * formatted view at all -- see `canFormat` below. Formatting is a
 * credibility signal, and withholding it from a known fabrication is the
 * same rule that refuses to WRITE an essay from a failed answer in the
 * first place.
 *
 * Layout mirrors a chat turn on purpose: provenance, then evidence, then the
 * body, then the verdict. Citations sit above the essay because they arrive
 * first and are independently trustworthy; the verdict sits below because it
 * cannot exist until the essay does.
 */
import { useEffect, useState } from "react";

import Citations from "./Citations";
import GroundingBanner from "./GroundingBanner";
import type { Essay, EssayView } from "../types";

const STATUS: Record<string, string> = {
  sending: "Gathering evidence…",
  sourced: "Evidence found · writing…",
  streaming: "Writing…",
  verifying: "Verifying against sources…",
};

function clock(ms: number): string {
  const total = Math.round(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

/**
 * The document handed to the sandboxed iframe via `srcdoc`.
 *
 * `bodyHtml` is the ALREADY-SANITIZED string from `artifacts.render()` --
 * this function only wraps it in its own head (charset, CSP, typography).
 * Nothing here originates from untrusted input, so template interpolation
 * is safe at this one call site the same way it would not be anywhere else.
 *
 * The CSP is the frame's second independent isolation layer, on top of
 * `sandbox=""`: `default-src 'none'` means the frame cannot issue a single
 * network request regardless of what the sanitizer let through -- this,
 * not the sanitizer, is what makes the external-resource defence total.
 * `style-src 'unsafe-inline'` admits only the <style> block authored here;
 * no attribute survives sanitization for untrusted CSS to occupy anyway
 * (`artifacts.PERMITTED_TAGS` carries no attributes at all).
 */
function buildSrcDoc(bodyHtml: string): string {
  return `<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; form-action 'none'; base-uri 'none'">
<style>
  :root { --bg:#0f1115; --fg:#e6e9ef; --dim:#98a2b3; --line:#262b36; }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f7f8fa; --fg:#131720; --dim:#5b6472; --line:#e3e6ec; }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 4px 2px 20px;
    background: var(--bg); color: var(--fg);
    font: 15px/1.6 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  h1, h2, h3, h4 { line-height: 1.25; margin: 1.1em 0 .5em; }
  h1 { font-size: 1.5em; } h2 { font-size: 1.25em; } h3 { font-size: 1.1em; }
  p, ul, ol, blockquote, pre { margin: 0 0 .9em; }
  ul, ol { padding-left: 1.4em; }
  blockquote {
    margin: 0 0 .9em; padding: 2px 14px; border-left: 3px solid var(--line);
    color: var(--dim);
  }
  code {
    background: color-mix(in srgb, var(--fg) 8%, transparent);
    border-radius: 4px; padding: 0 4px; font-size: .9em;
  }
  pre { overflow-x: auto; }
  pre code { background: none; padding: 0; }
  hr { border: none; border-top: 1px solid var(--line); margin: 1.4em 0; }
</style>
</head>
<body>${bodyHtml}</body>
</html>`;
}

export default function ArtifactPane({
  open,
  onToggle,
  essay,
  history,
  onShow,
}: {
  open: boolean;
  onToggle: () => void;
  essay: EssayView | null;
  history: Essay[];
  onShow: (row: Essay) => void;
}) {
  const status = essay ? STATUS[essay.state] : undefined;
  const retracted = essay?.state === "retracted";
  const working = Boolean(status);

  // A retracted essay is never offered a rich view -- there is no toggle
  // to override that, not just a default that happens to favour source.
  const canFormat =
    !retracted && essay?.render?.rendered === true;

  const [viewOverride, setViewOverride] =
    useState<"formatted" | "source" | null>(null);
  // A manual toggle on essay A must not carry over and silently apply to
  // essay B once the user switches essays.
  useEffect(() => setViewOverride(null), [essay?.id]);

  const view = viewOverride ?? (canFormat ? "formatted" : "source");
  const removedCount =
    essay?.render?.rendered === true
      ? essay.render.blocked + essay.render.stripped
      : 0;

  return (
    <aside className={`artifact ${open ? "" : "collapsed"}`}>
      <div className="artifact-head">
        <span>Artifact Viewer</span>
        <button className="ghost" onClick={onToggle} aria-expanded={open}>
          {open ? "Hide" : "Show"}
        </button>
      </div>

      {open && (
        <div className="artifact-body">
          {!essay && (
            <>
              <p className="empty">No essay yet.</p>
              <p>
                Ask a question, then choose <strong>Write a Ship 30 essay</strong> on a
                verified answer. The essay is written from that answer's own evidence and
                cites the same sources.
              </p>
              <p className="dim">
                A finished essay renders formatted and isolated, inside a sandboxed{" "}
                <code>iframe</code> with no <code>allow-scripts</code> and no{" "}
                <code>allow-same-origin</code>, behind server-side sanitisation
                (<code>app/artifacts.py</code>) and a strict CSP. A retracted essay is
                always shown as plain source instead.
              </p>
            </>
          )}

          {essay && (
            <>
              <div className="artifact-meta">
                {/* Provenance stays visible, live and replayed alike: the model
                    and the writing instructions are part of the claim. */}
                {essay.provider && (
                  <span className="prov">
                    {essay.provider} · {essay.model}
                  </span>
                )}
                {essay.skill && <span className="prov"> · {essay.skill}</span>}
                <span className="prov">
                  {" · "}
                  {essay.wordCount.toLocaleString()} words
                  {essay.targetWords ? ` / ~${essay.targetWords.toLocaleString()}` : ""}
                </span>
                {essay.withinTarget === false && (
                  // Reported, never corrected. Truncating to length would cut
                  // quotes and citation tags mid-sentence.
                  <span className="prov off-target"> · off target</span>
                )}
                {essay.elapsedMs !== undefined && working && (
                  <span className="prov"> · {clock(essay.elapsedMs)}</span>
                )}
                {!working && essay.latencyMs !== undefined && (
                  <span className="prov"> · {clock(essay.latencyMs)}</span>
                )}
              </div>

              {/* Before the text, always. */}
              <Citations sources={essay.sources} />

              {status && (
                <div className="turn-status">
                  {status}
                  <span className="caret">▍</span>
                </div>
              )}

              {essay.markdown && !working && (
                <div className="artifact-view-toggle">
                  <button
                    className={`ghost row-toggle ${view === "formatted" ? "active" : ""}`}
                    disabled={!canFormat}
                    onClick={() => setViewOverride("formatted")}
                    title={
                      canFormat
                        ? "Sanitized, formatted render"
                        : retracted
                          ? "Withheld -- this essay failed verification"
                          : "Not available"
                    }
                  >
                    Formatted
                  </button>
                  <button
                    className={`ghost row-toggle ${view === "source" ? "active" : ""}`}
                    onClick={() => setViewOverride("source")}
                  >
                    Source
                  </button>
                  {view === "formatted" && removedCount > 0 && (
                    <span className="prov dim">
                      {" · "}
                      {removedCount} element{removedCount === 1 ? "" : "s"} removed by
                      the artifact policy
                    </span>
                  )}
                </div>
              )}

              {essay.markdown && view === "formatted" &&
                essay.render?.rendered === true && (
                  <iframe
                    className="essay-frame"
                    sandbox=""
                    referrerPolicy="no-referrer"
                    title="Rendered essay"
                    srcDoc={buildSrcDoc(essay.render.html)}
                  />
              )}

              {essay.markdown && view === "source" && (
                // A React text child, so the browser never parses it as markup.
                // This is the same escaping path the chat body uses.
                <pre className={`essay-body ${retracted ? "retracted-text" : ""}`}>
                  {essay.markdown}
                  {essay.state === "streaming" && <span className="caret">▍</span>}
                </pre>
              )}

              {essay.markdown && !working && essay.renderError && (
                <p className="prov dim artifact-render-note">
                  Formatted view unavailable ({essay.renderError.code}). Showing
                  source.
                </p>
              )}

              {essay.grounding && <GroundingBanner grounding={essay.grounding} />}

              {essay.error && (
                <div className="turn-error" role="alert">
                  <strong>{essay.error.code}</strong> — {essay.error.message}
                  {essay.error.retryable && (
                    <div className="hint">
                      Retryable on this session, and therefore on{" "}
                      {essay.provider ?? "the same provider"}. Nothing is switched for
                      you.
                    </div>
                  )}
                </div>
              )}
            </>
          )}

          {history.length > 1 && (
            <div className="artifact-history">
              <div className="sidebar-head">Essays in this session</div>
              {history.map((row) => (
                <button
                  key={row.id}
                  className={`ghost row ${row.id === essay?.id ? "active" : ""}`}
                  onClick={() => onShow(row)}
                >
                  {row.title ?? "Untitled essay"} · {row.word_count.toLocaleString()} words
                  {row.grounding && !row.grounding.grounded && " · retracted"}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </aside>
  );
}
