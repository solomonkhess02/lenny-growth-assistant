/**
 * Sessions, each labelled with the provider it is pinned to.
 *
 * The provider is shown per row rather than only on the active session,
 * because switching sessions is how a user switches provider -- so the label
 * is the thing being chosen between.
 */
import type { SessionSummary } from "../types";

export default function SessionList({
  sessions, activeId, onSelect, onDelete,
}: {
  sessions: SessionSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  if (sessions.length === 0) {
    return <p className="empty side">No sessions yet.</p>;
  }

  return (
    <ul className="session-list">
      {sessions.map((s) => (
        <li key={s.id} className={s.id === activeId ? "active" : ""}>
          <button className="session-open" onClick={() => onSelect(s.id)}>
            <span className="session-title">{s.title ?? `Session ${s.id.slice(0, 8)}`}</span>
            <span className="session-provider">
              {s.provider} · {s.model}
            </span>
          </button>
          <button
            className="ghost danger"
            aria-label="Delete session"
            title="Delete session"
            onClick={() => onDelete(s.id)}
          >
            ×
          </button>
        </li>
      ))}
    </ul>
  );
}
