/**
 * HTTP + SSE client.
 *
 * Extracted from the Phase 2B shell, where the stream parser was inlined in a
 * component. Kept deliberately dumb: it decodes frames and reports errors. It
 * takes no view on providers, never retries, and above all never falls back to
 * a different provider -- non-substitution is a property of the whole system,
 * and a "helpful" client retry is exactly how such a property gets lost.
 */
import type {
  Essay, Health, ProviderHealth, ProviderList, SessionDetail, SessionSummary,
  StreamError,
} from "./types";

export class ApiError extends Error {
  code: string;
  retryable: boolean;

  constructor(err: StreamError) {
    super(err.message);
    this.code = err.code;
    this.retryable = err.retryable;
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    // The backend's error envelope is {error: {code, message, retryable}}.
    const body = await res.json().catch(() => null);
    const err = body?.error;
    throw new ApiError({
      code: err?.code ?? "http_error",
      message: err?.message ?? `HTTP ${res.status}`,
      retryable: err?.retryable ?? false,
    });
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const getHealth = () => json<Health>("/api/health");
export const getProviders = () => json<ProviderList>("/api/providers");
export const checkProvider = (name: string) =>
  json<ProviderHealth>(`/api/providers/check?name=${encodeURIComponent(name)}`);

export const listSessions = () => json<SessionSummary[]>("/api/sessions");
export const getSession = (id: string) =>
  json<SessionDetail>(`/api/sessions/${id}`);
export const deleteSession = (id: string) =>
  json<void>(`/api/sessions/${id}`, { method: "DELETE" });

/**
 * Create a session, optionally on a specific provider.
 *
 * This is the ONLY place a provider is ever chosen. A session's provider is
 * immutable afterwards -- there is no update call here because there is no
 * route to call, by design.
 */
export const createSession = (provider?: string) =>
  json<SessionSummary>("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ title: null, user_metadata: {}, provider: provider ?? null }),
  });

export const listEssays = (sessionId: string) =>
  json<Essay[]>(`/api/sessions/${sessionId}/essays`);
export const getEssay = (id: string) => json<Essay>(`/api/essays/${id}`);

export type SseFrame = { event: string; data: any };

/**
 * POST a JSON body and yield SSE frames as they arrive.
 *
 * EventSource cannot POST, so the body is parsed by hand. Frames are split on
 * a blank line and only complete frames are emitted -- a partial frame stays
 * in the buffer, which is what makes token-by-token delivery safe.
 *
 * One implementation serves chat turns and essays alike, because they are the
 * same protocol. A second copy would be a second place for the framing to
 * drift, and a 1,250-word essay streamed over ten minutes is exactly where a
 * subtle buffering bug would surface first.
 */
async function* sseStream(
  path: string,
  payload: unknown,
  signal?: AbortSignal,
): AsyncGenerator<SseFrame> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });

  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => null);
    const err = body?.error;
    throw new ApiError({
      code: err?.code ?? "http_error",
      message: err?.message ?? `HTTP ${res.status}`,
      retryable: err?.retryable ?? false,
    });
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    const frames = buf.split("\n\n");
    buf = frames.pop() ?? ""; // trailing partial frame, if any
    for (const frame of frames) {
      let event = "message";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) yield { event, data: JSON.parse(data) };
    }
  }
}

/** Ask a question on a session. */
export const postMessage = (
  sessionId: string,
  content: string,
  signal?: AbortSignal,
) => sseStream(`/api/sessions/${sessionId}/messages`, { content }, signal);

/**
 * Turn an existing verified answer into a Ship 30 essay.
 *
 * Carries only the message id. The provider is a property of the session, so
 * the essay runs on whatever wrote the answer -- there is no field here that
 * could ask for anything else, and that absence is the guarantee.
 */
export const postEssay = (
  sessionId: string,
  sourceMessageId: string,
  signal?: AbortSignal,
) =>
  sseStream(
    `/api/sessions/${sessionId}/essays`,
    { source_message_id: sourceMessageId },
    signal,
  );
