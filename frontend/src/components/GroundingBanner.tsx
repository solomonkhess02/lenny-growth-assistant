/**
 * The retraction.
 *
 * Verification can only run once the answer exists, so a failed verdict always
 * arrives after the reader has already seen the text. The honest response to
 * that is to WITHDRAW what was said -- not to append a caution to it. A
 * footnote leaves a fabricated quote on screen looking like an answer with a
 * caveat; this states plainly that the answer is not trustworthy and names
 * exactly what did not check out.
 */
import type { Grounding } from "../types";

export default function GroundingBanner({ grounding }: { grounding: Grounding }) {
  if (grounding.grounded) {
    return (
      <div className="verdict pass" title="Every quote and citation was checked against the retrieved evidence">
        ✓ Verified against sources
        {grounding.quotes_found > 0 &&
          ` · ${grounding.quotes_found} quote${grounding.quotes_found === 1 ? "" : "s"} checked`}
      </div>
    );
  }

  return (
    <div className="verdict fail" role="alert">
      <div className="verdict-head">⚠ Answer retracted — it failed verification</div>
      <p className="verdict-body">
        This answer is <strong>not</strong> supported by the transcripts and should not be
        relied on. It has been withdrawn rather than shown with a caveat.
      </p>
      {grounding.fabricated_quotes.length > 0 && (
        <div className="verdict-detail">
          <div className="verdict-label">Quotes that appear nowhere in the evidence:</div>
          <ul>
            {grounding.fabricated_quotes.map((q, i) => (
              <li key={i}>“{q}”</li>
            ))}
          </ul>
        </div>
      )}
      {grounding.invalid_tags.length > 0 && (
        <div className="verdict-detail">
          <div className="verdict-label">Citations pointing at evidence that was never retrieved:</div>
          <ul>
            {grounding.invalid_tags.map((t) => (
              <li key={t}>[{t}]</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
