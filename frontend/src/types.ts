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

/**
 * A generated Ship 30 essay.
 *
 * Carries the same trust fields a message does, for the same reasons:
 * `sources` so the citations survive a reload, `grounding` so a failed verdict
 * still retracts the essay after a refresh, and provider/model/skill so the
 * artifact stays attributable to what wrote it.
 *
 * `markdown` is the raw generated source. It is rendered as ESCAPED TEXT in
 * Phase 6 -- never parsed to HTML, never injected. Phase 7 owns the isolation
 * policy that would let it be rendered.
 */
export type Essay = {
  id: string;
  session_id: string;
  source_message_id: string | null;
  title: string | null;
  markdown: string;
  format: string;
  word_count: number;
  provider: string;
  model: string;
  latency_ms: number | null;
  sources: Source[];
  grounding: Grounding | null;
  skill_name: string;
  skill_sha256: string;
  created_at: string;
};

/** The terminal payload of an essay stream. */
export type EssayDone = {
  essay_id: string;
  title: string | null;
  word_count: number;
  target_words: number;
  within_target: boolean;
  blockquote_lines: number;
  trustworthy: boolean;
  supported: boolean;
  latency_ms: number;
};

/**
 * An essay as the UI holds it, live or replayed.
 *
 * Reuses TurnState rather than defining a parallel vocabulary: the essay
 * stream is the same protocol, so it has the same states, and `retracted`
 * means the same thing in both places.
 */
export type EssayView = {
  id?: string;
  title: string | null;
  markdown: string;
  sources: Source[];
  grounding: Grounding | null;
  state: TurnState;
  error?: StreamError;
  provider?: string;
  model?: string;
  skill?: string;
  wordCount: number;
  targetWords?: number;
  withinTarget?: boolean;
  latencyMs?: number;
  /** Wall-clock since the request started. A 10-minute local generation needs
   *  to look like progress rather than a hang. */
  elapsedMs?: number;
};

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
  /** The persisted message id, once there is one.
   *
   *  Absent while a turn is still streaming, which is exactly the window in
   *  which it is not yet eligible to become an essay -- so the missing id and
   *  the missing eligibility line up rather than needing separate tracking. */
  id?: string;
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
