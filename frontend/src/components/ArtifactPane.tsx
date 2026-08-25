/**
 * Artifact Viewer -- Ship 30 essays, as escaped text (Phase 6).
 *
 * Phase 7 owns sanitization, isolation and CSP. Until that policy exists this
 * pane renders the essay the same way the chat renders an answer: as a React
 * text node, which React escapes. There is deliberately:
 *
 *   - no dangerouslySetInnerHTML
 *   - no iframe
 *   - no Markdown-to-HTML step, and no Markdown library in package.json
 *
 * So what a reader sees is the generated Markdown SOURCE. That is the honest
 * intermediate state: the essay is real, complete and verifiable now, and it
 * gains formatting when there is a stated policy for rendering untrusted
 * markup safely -- not before. Building the frame first and the security
 * policy second is safe in that order; the reverse is not.
 *
 * Layout mirrors a chat turn on purpose: provenance, then evidence, then the
 * body, then the verdict. Citations sit above the essay because they arrive
 * first and are independently trustworthy; the verdict sits below because it
 * cannot exist until the essay does.
 */
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
                Generated Markdown is shown as plain text. Rendering it stays disabled
                until Phase 7 defines the isolation policy — a sandboxed <code>iframe</code>{" "}
                without <code>allow-scripts</code> or <code>allow-same-origin</code>,
                server-side sanitisation, and a strict CSP.
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

              {essay.markdown && (
                // A React text child, so the browser never parses it as markup.
                // This is the same escaping path the chat body uses.
                <pre className={`essay-body ${retracted ? "retracted-text" : ""}`}>
                  {essay.markdown}
                  {essay.state === "streaming" && <span className="caret">▍</span>}
                </pre>
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
