/**
 * One turn.
 *
 * Assistant turns render in this order, top to bottom: provenance, evidence,
 * text, verdict. Evidence sits ABOVE the answer because it arrives first and
 * is independently trustworthy; the verdict sits below because it cannot exist
 * until the answer does.
 */
import Citations from "./Citations";
import GroundingBanner from "./GroundingBanner";
import type { Turn } from "../types";

const STATUS: Record<string, string> = {
  sending: "Retrieving evidence…",
  sourced: "Evidence found · generating…",
  streaming: "Generating…",
  verifying: "Verifying against sources…",
};

export default function Message({
  turn,
  onRetry,
  onWriteEssay,
  essayBusy,
  localProvider,
}: {
  turn: Turn;
  onRetry?: () => void;
  /** Present only when this turn is eligible to become an essay. */
  onWriteEssay?: () => void;
  essayBusy?: boolean;
  /** True when the session's provider is local, where an essay takes minutes. */
  localProvider?: boolean;
}) {
  if (turn.role === "user") {
    return (
      <div className="msg user">
        <div className="who">you</div>
        <div className="body">{turn.content}</div>
      </div>
    );
  }

  const retracted = turn.state === "retracted";
  const status = STATUS[turn.state];

  return (
    <div className={`msg assistant ${turn.state}`}>
      <div className="who">
        assistant
        {/* Provenance stays visible on every turn, including replayed
            history -- the model that produced a claim is part of the claim. */}
        {turn.provider && (
          <span className="prov">
            {" · "}
            {turn.provider} · {turn.model}
          </span>
        )}
        {turn.latencyMs !== undefined && (
          <span className="prov"> · {(turn.latencyMs / 1000).toFixed(1)}s</span>
        )}
      </div>

      {/* Before the text, always. */}
      <Citations sources={turn.sources} />

      {status && (
        <div className="turn-status">
          {status}
          <span className="caret">▍</span>
        </div>
      )}

      {turn.content && (
        <div className={`body ${retracted ? "retracted-text" : ""}`}>
          {turn.content}
          {turn.state === "streaming" && <span className="caret">▍</span>}
        </div>
      )}

      {turn.state === "abstained" && (
        <div className="abstained-note">
          No sufficiently relevant transcript material was found, so the assistant declined
          to answer rather than guess. This is the system working as intended.
        </div>
      )}

      {turn.grounding && turn.state !== "abstained" && (
        <GroundingBanner grounding={turn.grounding} />
      )}

      {/* Offered only on an answer that verified clean and cites evidence.
          An abstention has nothing to write from, and a retracted answer must
          not become the foundation of 1,250 more confident words. The server
          re-checks both -- a hidden button is not an access control. */}
      {onWriteEssay && (
        <div className="essay-row">
          <button className="ghost" onClick={onWriteEssay} disabled={essayBusy}>
            {essayBusy ? "Writing…" : "Write a Ship 30 essay"}
          </button>
          <span className="hint">
            ~1,250 words from these sources, on {turn.provider ?? "this session's model"}.
            {localProvider && " Local generation typically takes several minutes."}
          </span>
        </div>
      )}

      {turn.error && (
        <div className="turn-error" role="alert">
          <strong>{turn.error.code}</strong> — {turn.error.message}
          {turn.error.retryable && (
            <div className="retry-row">
              <span className="hint">
                Retryable. It will be reissued on this session — and therefore on{" "}
                {turn.provider ?? "the same provider"}. Nothing is switched for you.
              </span>
              {onRetry && (
                <button className="ghost" onClick={onRetry}>
                  Retry
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
