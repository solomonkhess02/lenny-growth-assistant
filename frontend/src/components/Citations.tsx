/**
 * The evidence an answer was built from.
 *
 * Rendered as soon as the `sources` event lands, which is BEFORE the first
 * token of the answer. That ordering is the point: these are rows the system
 * retrieved, not claims the model made, so they can be shown -- and trusted --
 * before any text exists.
 *
 * Every field here is copied from a stored transcript row. Nothing on this
 * card can be fabricated by a model, even one that is fabricating in the
 * answer text beside it.
 */
import type { Source } from "../types";

function timestamp(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export default function Citations({ sources }: { sources: Source[] }) {
  if (sources.length === 0) return null;

  return (
    <div className="citations">
      <div className="citations-head">
        Evidence · {sources.length} {sources.length === 1 ? "source" : "sources"}
      </div>
      <ol className="citation-list">
        {sources.map((s) => (
          <li key={s.label} className="citation">
            <span className="cite-label">[{s.label}]</span>
            <div className="cite-main">
              <a
                className="cite-title"
                href={s.citation_url}
                target="_blank"
                rel="noopener noreferrer"
                title="Opens the episode at the quoted moment"
              >
                {s.source_title}
              </a>
              <div className="cite-meta">
                {s.speaker}
                {" · "}
                <span className="cite-at">{timestamp(s.start_seconds)}</span>
                {" · "}
                <span title="Cosine similarity to the question">
                  {s.similarity.toFixed(2)}
                </span>
              </div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
