/**
 * Essay state for one session.
 *
 * Reuses the chat state machine rather than defining a parallel one, because
 * the essay stream is the same protocol:
 *
 *   meta      -> sending
 *   sources   -> sourced      (citations on screen, no text yet)
 *   delta     -> streaming
 *   grounding -> verifying
 *   done      -> done | retracted
 *   error     -> error
 *
 * The one thing this hook adds is a clock. On the local path a Ship 30 essay
 * takes minutes -- Phase 1 measured 619s for this exact task on qwen3:4b --
 * and a screen that shows text arriving but no elapsed time is indistinguish-
 * able from one that has quietly hung. Streaming is the contract; making a
 * ten-minute stream legible is what the contract is for.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, getEssayRender, listEssays, postEssay } from "./api";
import type { Essay, EssayView, Grounding, Source } from "./types";

/** Rebuild a stored essay into the shape the pane renders. */
export function toView(essay: Essay): EssayView {
  return {
    id: essay.id,
    title: essay.title,
    markdown: essay.markdown,
    sources: essay.sources ?? [],
    grounding: essay.grounding,
    // A stored FAIL still reads as a retraction after a reload. Same rule as
    // a replayed answer: a verdict that does not survive a refresh is not a
    // verdict, it is a decoration.
    state: essay.grounding && !essay.grounding.grounded ? "retracted" : "done",
    provider: essay.provider,
    model: essay.model,
    skill: essay.skill_name,
    wordCount: essay.word_count,
    latencyMs: essay.latency_ms ?? undefined,
  };
}

export function useEssay(sessionId: string | null) {
  const [essay, setEssay] = useState<EssayView | null>(null);
  const [history, setHistory] = useState<Essay[]>([]);
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopClock = useCallback(() => {
    if (timer.current !== null) {
      clearInterval(timer.current);
      timer.current = null;
    }
  }, []);

  /**
   * Phase 7: fetch the sanitized render for one essay and attach it, but
   * only if the pane is still showing that essay when the fetch resolves --
   * otherwise a slow render for an essay the user has since navigated away
   * from would land on whatever is showing now.
   *
   * A rejection (oversized artifact, unsupported format, a sanitizer fault,
   * the render endpoint's own post-render safety re-scan tripping) is
   * caught and kept as `renderError` rather than swallowed: the pane still
   * falls back to the escaped-source view either way -- that IS the
   * fail-closed contract -- but skill 06 also requires the REASON to be
   * surfaced, not silently dropped.
   */
  const fetchRender = useCallback((essayId: string) => {
    getEssayRender(essayId)
      .then((render) => {
        setEssay((prev) =>
          prev && prev.id === essayId
            ? { ...prev, render, renderError: undefined }
            : prev);
      })
      .catch((e: unknown) => {
        const err =
          e instanceof ApiError
            ? { code: e.code, message: e.message, retryable: e.retryable }
            : { code: "network_error", message: String(e), retryable: true };
        setEssay((prev) =>
          prev && prev.id === essayId
            ? { ...prev, render: undefined, renderError: err }
            : prev);
      });
  }, []);

  // Existing essays for this session, so a reload does not lose one that cost
  // ten minutes to produce.
  useEffect(() => {
    let cancelled = false;
    setEssay(null);
    setHistory([]);
    if (!sessionId) return;

    listEssays(sessionId)
      .then((rows) => {
        if (cancelled) return;
        setHistory(rows);
        if (rows.length > 0) {
          setEssay(toView(rows[0]));
          fetchRender(rows[0].id);
        }
      })
      .catch(() => {
        // A failure to list old essays must not break the page; the pane
        // simply shows its empty state.
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, fetchRender]);

  useEffect(() => stopClock, [stopClock]);

  const patch = useCallback((p: Partial<EssayView>) => {
    setEssay((prev) => (prev ? { ...prev, ...p } : prev));
  }, []);

  const generate = useCallback(
    async (sourceMessageId: string) => {
      if (!sessionId || busy) return;
      setBusy(true);

      const startedAt = Date.now();
      setEssay({
        title: null,
        markdown: "",
        sources: [],
        grounding: null,
        state: "sending",
        wordCount: 0,
        elapsedMs: 0,
      });

      stopClock();
      timer.current = setInterval(
        () => patch({ elapsedMs: Date.now() - startedAt }),
        1000,
      );

      const controller = new AbortController();
      abort.current = controller;

      // Accumulated locally: React state updates are async, and the terminal
      // event has to reason about what actually arrived.
      let grounding: Grounding | null = null;
      let markdown = "";

      try {
        for await (const { event, data } of postEssay(
          sessionId,
          sourceMessageId,
          controller.signal,
        )) {
          if (event === "meta") {
            patch({ provider: data.provider, model: data.model, skill: data.skill });
          } else if (event === "sources") {
            // Before any text, always -- the same ordering the answer uses,
            // for the same reason: these are retrieved rows, not model claims.
            patch({ sources: (data.sources ?? []) as Source[], state: "sourced" });
          } else if (event === "delta") {
            markdown += data.text;
            patch({
              markdown,
              // Recomputed as it streams so the reader can watch it approach
              // the target instead of waiting to find out.
              wordCount: markdown.split(/\s+/).filter(Boolean).length,
              state: "streaming",
            });
          } else if (event === "grounding") {
            grounding = data as Grounding;
            patch({ grounding, state: "verifying" });
          } else if (event === "error") {
            patch({ state: "error", error: data });
          } else if (event === "done") {
            patch({
              id: data.essay_id,
              title: data.title,
              // The server's count, not the browser's: one definition of a
              // word, and it is the one that was stored.
              wordCount: data.word_count,
              targetWords: data.target_words,
              withinTarget: data.within_target,
              latencyMs: data.latency_ms,
              elapsedMs: Date.now() - startedAt,
              state: grounding && !grounding.grounded ? "retracted" : "done",
            });
            if (sessionId) {
              listEssays(sessionId).then(setHistory).catch(() => {});
            }
            // Fire-and-forget: the essay is already `done`/`retracted`
            // per the TurnState machine above, unchanged by whether this
            // resolves, fails, or is still in flight.
            fetchRender(data.essay_id);
          }
        }
      } catch (e) {
        // Surfaced as-is. No provider is swapped in and no request is
        // silently reissued -- an essay is a shareable artifact, so a
        // substituted model would travel further than a substituted answer.
        const err =
          e instanceof ApiError
            ? { code: e.code, message: e.message, retryable: e.retryable }
            : { code: "network_error", message: String(e), retryable: true };
        patch({ state: "error", error: err });
      } finally {
        stopClock();
        abort.current = null;
        setBusy(false);
      }
    },
    [sessionId, busy, patch, stopClock, fetchRender],
  );

  const stop = useCallback(() => abort.current?.abort(), []);
  const show = useCallback(
    (row: Essay) => {
      setEssay(toView(row));
      fetchRender(row.id);
    },
    [fetchRender],
  );

  return { essay, history, busy, generate, stop, show };
}
