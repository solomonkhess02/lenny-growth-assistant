/**
 * Phase 5 chat UI.
 *
 * Four properties of the backend drive this layout, and none of them are
 * cosmetic:
 *
 *   1. The selected provider is always visible. A session is pinned to one
 *      provider for life, so the header shows the ACTIVE SESSION's provider --
 *      not the deployment default, which may differ.
 *   2. Nothing is ever substituted. There is no client-side fallback path: a
 *      failed turn surfaces its error and stops.
 *   3. Citations arrive before text and are rendered before text.
 *   4. A failed verdict retracts the answer (see GroundingBanner).
 *
 * The Artifact Viewer is layout only; Phase 7 owns its isolation policy.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { checkProvider, createSession, deleteSession, getHealth, listSessions } from "./api";
import ArtifactPane from "./components/ArtifactPane";
import Composer from "./components/Composer";
import Message from "./components/Message";
import NewSessionControl from "./components/NewSessionControl";
import SessionList from "./components/SessionList";
import { useChat } from "./useChat";
import { useEssay } from "./useEssay";
import type { Health, ProviderHealth, SessionSummary } from "./types";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [artifactOpen, setArtifactOpen] = useState(true);
  const endRef = useRef<HTMLDivElement>(null);

  const [activeHealth, setActiveHealth] = useState<ProviderHealth | null>(null);

  const { turns, busy, loadError, send, retry } = useChat(activeId);
  const {
    essay, history: essays, busy: essayBusy, generate, show: showEssay,
  } = useEssay(activeId);
  const active = sessions.find((s) => s.id === activeId) ?? null;

  const refreshHealth = useCallback(async () => {
    try {
      setHealth(await getHealth());
    } catch {
      setHealth(null);
    }
  }, []);

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await listSessions());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    refreshHealth();
    refreshSessions();
    const t = setInterval(refreshHealth, 15000);
    return () => clearInterval(t);
  }, [refreshHealth, refreshSessions]);

  // The header must describe the provider THIS session actually uses, which
  // is not necessarily the deployment default. Reporting the default's health
  // next to another provider's name is a quietly misleading combination.
  useEffect(() => {
    let cancelled = false;
    if (!active) {
      setActiveHealth(null);
      return;
    }
    const probe = () =>
      checkProvider(active.provider)
        .then((h) => {
          if (!cancelled) setActiveHealth(h);
        })
        .catch(() => {
          if (!cancelled) setActiveHealth(null);
        });
    probe();
    const t = setInterval(probe, 15000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [active]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  // An essay that takes minutes must not be generating behind a collapsed
  // pane -- the reader would have no way to tell it from nothing happening.
  const writeEssay = useCallback(
    (messageId: string) => {
      setArtifactOpen(true);
      void generate(messageId);
    },
    [generate],
  );

  /**
   * Is this turn eligible to become an essay?
   *
   * Verified clean, cites evidence, finished, and persisted. The server
   * re-checks every one of these -- this only decides whether to offer it.
   */
  const canWriteEssay = (t: (typeof turns)[number]) =>
    t.role === "assistant" &&
    t.state === "done" &&
    t.sources.length > 0 &&
    Boolean(t.id);

  const startSession = useCallback(
    async (provider: string) => {
      setError(null);
      try {
        const s = await createSession(provider);
        setSessions((prev) => [s, ...prev]);
        setActiveId(s.id);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [],
  );

  const removeSession = useCallback(
    async (id: string) => {
      try {
        await deleteSession(id);
        setSessions((prev) => prev.filter((s) => s.id !== id));
        setActiveId((cur) => (cur === id ? null : cur));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [],
  );

  const dbOk = health?.database.ok ?? false;
  const p = health?.provider;
  const defaultProviderOk = Boolean(p?.reachable && p?.model_available);
  const activeOk = Boolean(activeHealth?.reachable && activeHealth?.model_available);

  return (
    <div className="app">
      <header className="bar">
        <div className="brand">The Lenny Growth Assistant</div>
        <div className="status">
          {/* The ACTIVE SESSION's provider. Immutable, so it is a statement of
              fact about every turn below, not a current setting. */}
          {active ? (
            <span
              className={`pill ${activeHealth === null ? "" : activeOk ? "ok" : "warn"}`}
              title={
                activeHealth && !activeOk
                  ? (activeHealth.detail ??
                    "This session's provider is not reachable. Turns will fail; nothing is substituted.")
                  : "Fixed for this session. Start a new session to use another provider."
              }
            >
              {active.provider} · {active.model}
              {activeHealth && !activeOk && " · unavailable"}
            </span>
          ) : (
            <span
              className={`pill ${defaultProviderOk ? "ok" : "warn"}`}
              title={p?.detail ?? "Health of the deployment's default provider"}
            >
              no session · default {p ? p.provider : "unknown"}{" "}
              {defaultProviderOk ? "ready" : "unavailable"}
            </span>
          )}
          <span className={`pill ${dbOk ? "ok" : "bad"}`}>db {dbOk ? "ok" : "down"}</span>
          <NewSessionControl onCreate={startSession} disabled={busy} />
        </div>
      </header>

      {error && <div className="error">⚠ {error}</div>}
      {loadError && <div className="error">⚠ Could not load this session: {loadError}</div>}
      {active && activeHealth && !activeOk && (
        <div className="notice">
          {activeHealth.detail ??
            `This session runs on ${active.provider}, which is not reachable.`}{" "}
          Turns will fail with an error — no other provider is substituted. To use a different
          provider, start a new session.
        </div>
      )}
      {!active && health?.status === "degraded" && (
        <div className="notice">
          {p?.detail ?? "The default provider is not reachable."} Sessions on it will fail —
          nothing is substituted automatically.
        </div>
      )}

      <main className="split">
        <nav className="sidebar">
          <div className="sidebar-head">Sessions</div>
          <SessionList
            sessions={sessions}
            activeId={activeId}
            onSelect={setActiveId}
            onDelete={removeSession}
          />
        </nav>

        <section className="chat">
          <div className="messages">
            {!activeId && (
              <p className="empty">
                Start a session to begin. You choose the model provider when the session is
                created; it stays fixed for that whole conversation.
              </p>
            )}
            {activeId && turns.length === 0 && (
              <p className="empty">
                Session <code>{activeId.slice(0, 8)}</code> on{" "}
                <strong>{active?.provider}</strong>. Ask a question about product or growth —
                answers are grounded in Lenny's Podcast transcripts and cite their sources.
              </p>
            )}
            {turns.map((t, i) => (
              <Message
                key={i}
                turn={t}
                onRetry={i === turns.length - 1 && !busy ? retry : undefined}
                onWriteEssay={
                  canWriteEssay(t) ? () => writeEssay(t.id as string) : undefined
                }
                essayBusy={essayBusy}
                localProvider={active?.provider === "ollama"}
              />
            ))}
            <div ref={endRef} />
          </div>
          <Composer onSend={send} busy={busy} disabled={!activeId} />
        </section>

        <ArtifactPane
          open={artifactOpen}
          onToggle={() => setArtifactOpen((v) => !v)}
          essay={essay}
          history={essays}
          onShow={showEssay}
        />
      </main>
    </div>
  );
}
