/**
 * Artifact Viewer — layout and plumbing only (Phase 5).
 *
 * Phase 7 owns sanitization, isolation and CSP. Until that policy exists this
 * pane deliberately renders NO untrusted content: no dangerouslySetInnerHTML,
 * no iframe, no Markdown-to-HTML. Building the frame now and the security
 * policy later is safe in that order; the reverse is not.
 */
export default function ArtifactPane({
  open, onToggle,
}: {
  open: boolean;
  onToggle: () => void;
}) {
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
          <p className="empty">No artifact yet.</p>
          <p>
            Ship 30 essays are generated in Phase 6 and rendered here. Rendering stays
            disabled until Phase 7 defines the isolation policy — a sandboxed{" "}
            <code>iframe</code> without <code>allow-scripts</code> or{" "}
            <code>allow-same-origin</code>, server-side sanitisation, and a strict CSP.
          </p>
          <p className="dim">
            Generated HTML is untrusted input. This pane will not render it before that
            policy exists.
          </p>
        </div>
      )}
    </aside>
  );
}
