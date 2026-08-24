/**
 * Phase 2B frontend shell.
 *
 * Deliberately minimal: split pane, provider indicator, streaming chat against
 * the skeleton. The Artifact Viewer pane is a placeholder — artifacts arrive in
 * Phase 7, and sandboxing is decided but not yet implemented.
 *
 * Provider UX contract requirement 3 (provider always visible) is honoured
 * here from the start rather than bolted on in Phase 5.
 */
import { useCallback, useEffect, useRef, useState } from "react";

type Msg = {
  role: "user" | "assistant";
  content: string;
  provider?: string | null;
  model?: string | null;
  streaming?: boolean;
};

type Health = {
  status: string;
  version: string;
  database: { ok: boolean; detail?: string };
  provider: {
    provider: string;
    model: string;
    reachable?: boolean;
    model_available?: boolean;
    detail?: string;
  };
};

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const refreshHealth = useCallback(async () => {
    try {
      const r = await fetch("/api/health");
      setHealth(await r.json());
    } catch {
      setHealth(null);
    }
  }, []);

  useEffect(() => {
    refreshHealth();
    const t = setInterval(refreshHealth, 15000);
    return () => clearInterval(t);
  }, [refreshHealth]);

  const newSession = useCallback(async () => {
    setError(null);
    try {
      const r = await fetch("/api/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: null, user_metadata: {} }),
      });
      if (!r.ok) throw new Error((await r.json())?.error?.message ?? r.statusText);
      const s = await r.json();
      setSessionId(s.id);
      setMessages([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    if (!sessionId) newSession();
  }, [sessionId, newSession]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send() {
    const text = input.trim();
    if (!text || !sessionId || busy) return;
    setInput("");
    setError(null);
    setBusy(true);
    setMessages((m) => [
      ...m,
      { role: "user", content: text },
      { role: "assistant", content: "", streaming: true },
    ]);

    try {
      const r = await fetch(`/api/sessions/${sessionId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: text }),
      });
      if (!r.ok || !r.body) {
        const j = await r.json().catch(() => null);
        throw new Error(j?.error?.message ?? `HTTP ${r.status}`);
      }

      // Minimal SSE reader. EventSource cannot POST, so we parse the stream.
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const frames = buf.split("\n\n");
        buf = frames.pop() ?? "";
        for (const frame of frames) {
          let ev = "message";
          let data = "";
          for (const line of frame.split("\n")) {
            if (line.startsWith("event:")) ev = line.slice(6).trim();
            else if (line.startsWith("data:")) data += line.slice(5).trim();
          }
          if (!data) continue;
          const payload = JSON.parse(data);
          if (ev === "meta") {
            setMessages((m) => {
              const c = [...m];
              const last = c[c.length - 1];
              if (last) {
                last.provider = payload.provider;
                last.model = payload.model;
              }
              return c;
            });
          } else if (ev === "delta") {
            setMessages((m) => {
              const c = [...m];
              const last = c[c.length - 1];
              if (last) last.content += payload.text;
              return c;
            });
          } else if (ev === "error") {
            setError(payload.message);
            setMessages((m) => {
              const c = [...m];
              const last = c[c.length - 1];
              if (last) last.streaming = false;
              return c;
            });
          } else if (ev === "done") {
            setMessages((m) => {
              const c = [...m];
              const last = c[c.length - 1];
              if (last) last.streaming = false;
              return c;
            });
          }
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setMessages((m) =>
        m.map((x, i) => (i === m.length - 1 ? { ...x, streaming: false } : x))
      );
    }
  }

  const p = health?.provider;
  const providerOk = p?.reachable && p?.model_available;

  return (
    <div className="app">
      <header className="bar">
        <div className="brand">
          The Lenny Growth Assistant
          <span className="phase">Phase 2B skeleton</span>
        </div>
        <div className="status">
          {/* Requirement 3: provider selection always visible. */}
          <span className={`pill ${providerOk ? "ok" : "warn"}`} title={p?.detail ?? ""}>
            {p ? `${p.provider} · ${p.model}` : "provider unknown"}
          </span>
          <span className={`pill ${health?.database.ok ? "ok" : "bad"}`}>
            db {health?.database.ok ? "ok" : "down"}
          </span>
          <button onClick={newSession}>New session</button>
        </div>
      </header>

      {error && <div className="error">⚠ {error}</div>}
      {p && !providerOk && (
        <div className="notice">
          {p.detail ?? "Model provider is not ready. Generation will fail."}
        </div>
      )}

      <main className="split">
        <section className="chat">
          <div className="messages">
            {messages.length === 0 && (
              <p className="empty">
                Session <code>{sessionId?.slice(0, 8) ?? "…"}</code> ready. Retrieval
                and generation arrive in Phases 3–4; this pane exercises streaming
                and persistence.
              </p>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`msg ${m.role}`}>
                <div className="who">
                  {m.role}
                  {m.role === "assistant" && m.model && (
                    <span className="prov"> · {m.provider} · {m.model}</span>
                  )}
                </div>
                <div className="body">
                  {m.content}
                  {m.streaming && <span className="caret">▍</span>}
                </div>
              </div>
            ))}
            <div ref={endRef} />
          </div>
          <div className="composer">
            <textarea
              value={input}
              placeholder="Ask a product or growth question…"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              rows={2}
            />
            <button onClick={send} disabled={busy || !input.trim()}>
              {busy ? "Streaming…" : "Send"}
            </button>
          </div>
        </section>

        <aside className="artifact">
          <div className="artifact-head">Artifact Viewer</div>
          <div className="artifact-body">
            <p>
              Reserved for generated Markdown and HTML artifacts (Phase 7).
              Rendering will use a sandboxed <code>iframe</code> without
              <code> allow-scripts</code> or <code>allow-same-origin</code>, plus
              server-side sanitisation and a strict CSP.
            </p>
          </div>
        </aside>
      </main>
    </div>
  );
}
