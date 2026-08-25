/**
 * Conversation state for one session.
 *
 * The Phase 2B shell tracked a single boolean (`busy`). That was enough to
 * grey out a button and nothing else -- in particular it could not express
 * "the answer finished streaming but has not been verified yet", which is the
 * window in which a retraction becomes necessary.
 *
 * The event ordering maps onto states directly:
 *
 *   meta      -> sending
 *   sources   -> sourced      (citations on screen, no text yet)
 *   delta     -> streaming
 *   grounding -> done | retracted
 *   done      -> abstained, if the turn produced no evidence
 *   error     -> error
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, getSession, postMessage } from "./api";
import type { Grounding, Source, StoredMessage, Turn } from "./types";

/**
 * Was a stored assistant turn an abstention?
 *
 * Derived rather than stored, and sound because of a structural guarantee in
 * the agent: when retrieval returns nothing the model is never invoked, and
 * when it returns anything there is at least one source. So an assistant turn
 * that was verified but cites nothing can only be the abstention path.
 *
 * (backend/app/agent.py -- "No evidence, no answer.")
 */
function wasAbstention(m: StoredMessage): boolean {
  return m.role === "assistant" && m.sources.length === 0 && m.grounding !== null;
}

function replayState(m: StoredMessage): Turn["state"] {
  if (m.grounding && !m.grounding.grounded) return "retracted";
  if (wasAbstention(m)) return "abstained";
  return "done";
}

/** Rebuild the transcript from persisted rows, verdicts and citations intact. */
function toTurns(messages: StoredMessage[]): Turn[] {
  return messages
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => ({
      role: m.role as "user" | "assistant",
      content: m.content,
      provider: m.provider ?? undefined,
      model: m.model ?? undefined,
      sources: m.sources ?? [],
      grounding: m.grounding,
      state: m.role === "assistant" ? replayState(m) : ("done" as const),
      latencyMs: m.latency_ms ?? undefined,
    }));
}

export function useChat(sessionId: string | null) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const lastSent = useRef<string | null>(null);

  // Load history whenever the active session changes. Citations and verdicts
  // come back with it, so a reopened session looks like it did live.
  useEffect(() => {
    let cancelled = false;
    if (!sessionId) {
      setTurns([]);
      return;
    }
    setLoadError(null);
    getSession(sessionId)
      .then((s) => {
        if (!cancelled) setTurns(toTurns(s.messages));
      })
      .catch((e) => {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  /** Mutate the in-flight assistant turn (always the last one). */
  const patchLast = useCallback((patch: Partial<Turn>) => {
    setTurns((prev) => {
      if (prev.length === 0) return prev;
      const next = [...prev];
      next[next.length - 1] = { ...next[next.length - 1], ...patch };
      return next;
    });
  }, []);

  const send = useCallback(
    async (text: string) => {
      if (!sessionId || busy) return;
      lastSent.current = text;
      setBusy(true);

      setTurns((prev) => [
        ...prev,
        { role: "user", content: text, sources: [], grounding: null, state: "done" },
        { role: "assistant", content: "", sources: [], grounding: null, state: "sending" },
      ]);

      const controller = new AbortController();
      abort.current = controller;

      // Accumulated locally: React state updates are async, and the terminal
      // event needs to reason about what actually arrived.
      let sources: Source[] = [];
      let grounding: Grounding | null = null;
      let text_ = "";

      try {
        for await (const { event, data } of postMessage(sessionId, text, controller.signal)) {
          if (event === "meta") {
            patchLast({ provider: data.provider, model: data.model });
          } else if (event === "sources") {
            // Before any text, always. This is the ordering the backend
            // guarantees and the reason citations can be trusted on sight.
            sources = data.sources ?? [];
            patchLast({ sources, state: "sourced" });
          } else if (event === "delta") {
            text_ += data.text;
            patchLast({ content: text_, state: "streaming" });
          } else if (event === "grounding") {
            grounding = data as Grounding;
            patchLast({ grounding, state: "verifying" });
          } else if (event === "error") {
            patchLast({ state: "error", error: data });
          } else if (event === "done") {
            patchLast({
              state: data.abstained
                ? "abstained"
                : grounding && !grounding.grounded
                  ? "retracted"
                  : "done",
              latencyMs: data.latency_ms,
            });
          }
        }
      } catch (e) {
        // A failure here is surfaced as-is. No provider is swapped in, and no
        // request is silently reissued.
        const err =
          e instanceof ApiError
            ? { code: e.code, message: e.message, retryable: e.retryable }
            : { code: "network_error", message: String(e), retryable: true };
        patchLast({ state: "error", error: err });
      } finally {
        abort.current = null;
        setBusy(false);
      }
    },
    [sessionId, busy, patchLast],
  );

  const stop = useCallback(() => abort.current?.abort(), []);

  /**
   * Reissue the last question on the SAME session.
   *
   * Retry cannot reach a different provider even in principle: the provider is
   * a property of the session, and this posts to the same session. There is no
   * "try the other one" path to accidentally build.
   *
   * The failed attempt is left in the transcript rather than erased -- the user
   * turn was already committed server-side, and a conversation that failed once
   * genuinely did.
   */
  const retry = useCallback(() => {
    const text = lastSent.current;
    if (text) void send(text);
  }, [send]);

  return { turns, busy, loadError, send, retry, stop };
}
