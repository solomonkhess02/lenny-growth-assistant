/**
 * Wire types, mirroring the backend contracts exactly.
 *
 * The SSE protocol is `meta -> sources -> delta* -> grounding -> done | error`
 * (backend/app/routers/chat.py). Two orderings in it are load-bearing rather
 * than incidental, and the UI is built around both:
 *
 *   - `sources` arrives BEFORE any text, because citations are evidence the
 *     system retrieved, not claims the model made. They are trustworthy before
 *     a single token exists, so they are shown first.
 *   - `grounding` necessarily arrives AFTER the text it verifies. An answer
 *     cannot be checked before it exists. That is why a failed verdict is a
 *     RETRACTION of something already on screen, not a footnote under it.
 */

/** One citable span. Every field originates in a stored row, never in model output. */
export type Source = {
  label: string; // "E1", "E2" -- matches the [E1] tags in the answer text
  source_id: string;
  source_title: string;
  guest: string;
  speaker: string;
  citation_url: string; // deep-links to the quoted moment
  start_seconds: number;
  publish_date: string | null;
  similarity: number;
};

/** The verification verdict for one answer. */
export type Grounding = {
  verdict: "PASS" | "FAIL";
  grounded: boolean;
  quotes_found: number;
  fabricated_quotes: string[];
  tags_found: string[];
  invalid_tags: string[];
};

export type StreamError = {
  code: string;
  message: string;
  retryable: boolean;
};

export type SessionSummary = {
  id: string;
  title: string | null;
  provider: string;
  model: string;
  created_at: string;
  updated_at: string;
};

/** A persisted turn, as returned by GET /api/sessions/{id}. */
export type StoredMessage = {
  id: string;
  session_id: string;
  seq: number;
  role: "user" | "assistant" | "system";
  content: string;
  provider: string | null;
  model: string | null;
  latency_ms: number | null;
  sources: Source[];
  grounding: Grounding | null;
  created_at: string;
};

export type SessionDetail = SessionSummary & { messages: StoredMessage[] };

export type ProviderList = {
  selected: string;
  available: string[];
  detail: string;
};

export type ProviderHealth = {
  provider: string;
  model: string;
  base_url: string;
  configured: boolean;
  reachable?: boolean;
  model_available?: boolean;
  detail?: string;
  latency_ms?: number;
};

export type Health = {
  status: "ok" | "degraded" | "unhealthy" | string;
  version: string;
  database: { ok: boolean; detail?: string };
  provider: ProviderHealth;
};

/**
 * The lifecycle of one assistant turn.
 *
 * `retracted` and `abstained` are deliberately NOT collapsed into `error`.
 * They are three different things and a reader must be able to tell them
 * apart: the system failed (error), the system answered and the answer cannot
 * be trusted (retracted), or the system declined to answer because the
 * transcripts do not support the question (abstained -- which is the product
 * working correctly).
 */
export type TurnState =
  | "sending" // request in flight, nothing back yet
  | "sourced" // evidence in hand, no text yet
  | "streaming" // text arriving
  | "verifying" // text complete, verdict pending
  | "done" // verified clean
  | "retracted" // verified FAILED -- the answer is withdrawn
  | "abstained" // no evidence; the model was never invoked
  | "error"; // the turn failed

export type Turn = {
  role: "user" | "assistant";
  content: string;
  provider?: string;
  model?: string;
  sources: Source[];
  grounding: Grounding | null;
  state: TurnState;
  error?: StreamError;
  latencyMs?: number;
};
