"""The agent layer: retrieval -> prompt -> generation -> grounding.

Boundary chain (skill 03):
    API -> session -> agent -> retrieval -> prompt -> provider -> grounding

Everything deterministic stays deterministic. Retrieval is a database query,
not a tool the model chooses to call. The model does exactly one thing:
turn evidence it was handed into prose. It cannot decide what to retrieve, it
has no tools, and it never sees a source it was not given.

Two rules are enforced structurally rather than by prompting, because a prompt
is a request and this product's trust property cannot rest on a request:

  1. **No evidence, no answer.** When retrieval returns nothing, the model is
     never called at all. Abstention is not something the model can decline to
     do -- there is no generation step to decline.
  2. **Every answer is verified.** `grounding.verify_answer` runs on all
     generated output. A fabricated quote or a citation tag pointing at
     evidence that does not exist is detected and surfaced, never silently
     passed through.

Why generation runs on the provider seam rather than the Claude Agent SDK:
see docs/agent-layer-decision.md. Short version -- the SDK's harness prompt is
an irreducible ~24.5K tokens, against a locked 8,192-token local context on a
4 GB card. It cannot coexist with the mandated local demo.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .grounding import QuoteReport, verify_answer
from .providers import ModelProvider, get_provider
from .retrieval import Evidence, retrieve, retrieve_for_session

log = logging.getLogger("app.agent")

# Returned verbatim when retrieval finds nothing. Deliberately a constant and
# not model output: the one answer that must never be creative.
ABSTENTION = (
    "The transcript material I have does not support an answer to that. "
    "I searched the indexed episodes of Lenny's Podcast and found nothing "
    "relevant enough to cite, so rather than guess I would rather tell you "
    "plainly that I don't have it."
)

SYSTEM_PROMPT = """\
You answer product and growth questions using ONLY the numbered evidence \
provided by the user's message. The evidence comes from transcripts of \
Lenny's Podcast.

Rules, in order of importance:

1. Use ONLY the provided evidence. If it does not contain the answer, say so \
plainly. Never rely on your own knowledge of the subject.
2. Cite with square-bracket tags that match the evidence numbers exactly: \
[E1], [E2]. NEVER cite a number that was not provided to you.
3. If you quote, the quoted words must appear VERBATIM in the evidence. Do \
not paraphrase inside quotation marks. If you cannot quote exactly, do not \
use quotation marks at all.
4. Never invent a speaker, an episode title, a company, or a statistic that \
is not in the evidence.
5. Be direct and concrete. Lead with the answer. Keep it under 250 words \
unless the question genuinely needs more.
"""


@dataclass
class AnswerResult:
    answer: str
    evidence: list[Evidence]
    grounding: QuoteReport
    provider: str
    model: str
    latency_ms: int
    abstained: bool
    sources: list[dict] = field(default_factory=list)

    @property
    def supported(self) -> bool:
        """Was there evidence to answer from at all?"""
        return bool(self.evidence)

    @property
    def trustworthy(self) -> bool:
        """Answered from evidence AND verified clean."""
        return self.supported and self.grounding.grounded

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "abstained": self.abstained,
            "supported": self.supported,
            "trustworthy": self.trustworthy,
            "grounding": self.grounding.to_dict(),
            "provider": self.provider,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "sources": self.sources,
            "evidence": [e.to_dict() for e in self.evidence],
        }


def cite_label(index: int) -> str:
    """Evidence is 1-indexed by position: the first item is [E1]."""
    return f"E{index + 1}"


def source_summaries(evidence: list[Evidence]) -> list[dict]:
    """Citation cards for the UI, and the record a turn is rebuilt from.

    Every field is copied from a stored row. Nothing here is model output, so
    a source card cannot be fabricated even if the answer text is.

    `chunk_id` and `transcript_id` are keys, not presentation: they are what
    lets Phase 6 rehydrate the exact evidence a stored answer was written from
    (retrieval.evidence_by_chunk_ids) instead of re-running a search and
    silently substituting different material. They are stored-row identifiers
    like every other field here, so including them changes nothing about who
    authored this data.
    """
    return [
        {
            "label": cite_label(i),
            "source_id": e.source_id,
            "source_title": e.source_title,
            "guest": e.guest,
            "speaker": e.speaker,
            "citation_url": e.citation_url,
            "start_seconds": e.start_seconds,
            "publish_date": e.publish_date.isoformat() if e.publish_date else None,
            "similarity": e.similarity,
            "chunk_id": e.chunk_id,
            "transcript_id": e.transcript_id,
        }
        for i, e in enumerate(evidence)
    ]


def build_prompt(question: str, evidence: list[Evidence]) -> str:
    """Render the evidence block the model is allowed to draw on.

    Speaker and episode are included per chunk because they are part of what
    the model may legitimately quote, and because an answer that says "Jackson
    Shuttleworth explains..." should be able to get that from the evidence
    rather than from memory.
    """
    blocks = []
    for i, e in enumerate(evidence):
        blocks.append(
            f"[{cite_label(i)}] {e.speaker} on \"{e.source_title}\":\n{e.text}"
        )
    joined = "\n\n".join(blocks)
    return (
        f"Evidence:\n\n{joined}\n\n"
        f"---\n\nQuestion: {question}\n\n"
        f"Answer using only the evidence above, citing "
        f"{', '.join('[' + cite_label(i) + ']' for i in range(len(evidence)))}."
    )


async def _generate(provider: ModelProvider, prompt: str) -> str:
    parts: list[str] = []
    async for delta in provider.stream(f"{SYSTEM_PROMPT}\n\n{prompt}"):
        parts.append(delta)
    return "".join(parts).strip()


def _abstention_result(provider: ModelProvider, started: float) -> AnswerResult:
    return AnswerResult(
        answer=ABSTENTION,
        evidence=[],
        # Vacuously grounded: no quotes and no tags, because no model ran.
        grounding=verify_answer("", []),
        provider=provider.name,
        model=provider.model,
        latency_ms=int((time.perf_counter() - started) * 1000),
        abstained=True,
    )


async def answer_question(
    db: AsyncSession,
    question: str,
    *,
    session_id: uuid.UUID | None = None,
    k: int | None = None,
    provider: ModelProvider | None = None,
) -> AnswerResult:
    """Answer one question, grounded and verified. Never raises on ungrounded
    output -- it reports it, because hiding it is the failure mode."""
    settings = get_settings()
    provider = provider or get_provider()
    k = k or settings.retrieval_k
    t0 = time.perf_counter()

    evidence = (
        await retrieve_for_session(db, session_id, question, k)
        if session_id is not None
        else await retrieve(db, question, k)
    )

    if not evidence:
        # The model is never invoked. Abstention cannot be overridden by
        # generation because there is no generation.
        log.info("agent_abstained", extra={
            "reason": "no_evidence_above_floor",
            "min_similarity": settings.retrieval_min_similarity,
            "provider": provider.name, "outcome": "abstained"})
        return _abstention_result(provider, t0)

    text = await _generate(provider, build_prompt(question, evidence))

    # MANDATORY. Not conditional on provider, model, or configuration.
    report = verify_answer(text, evidence)
    latency = int((time.perf_counter() - t0) * 1000)

    log.info("agent_answered", extra={
        "provider": provider.name, "model": provider.model,
        "evidence_count": len(evidence),
        "sources": sorted({e.source_id for e in evidence}),
        "grounding_verdict": report.verdict,
        "fabricated_quotes": len(report.fabricated_quotes),
        "invalid_tags": report.invalid_tags,
        "duration_ms": latency,
        "outcome": "ok" if report.grounded else "ungrounded"})

    return AnswerResult(
        answer=text, evidence=evidence, grounding=report,
        provider=provider.name, model=provider.model,
        latency_ms=latency, abstained=False,
        sources=source_summaries(evidence),
    )


async def stream_answer(
    db: AsyncSession,
    question: str,
    *,
    session_id: uuid.UUID | None = None,
    k: int | None = None,
    provider: ModelProvider | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    """Yield (event_name, payload) pairs for the SSE transport.

    Events: sources -> delta* -> grounding -> complete

    Verification necessarily lands AFTER the text has streamed -- an answer
    cannot be checked before it exists. The streamed text is therefore
    provisional until the `grounding` event arrives, and the UI must treat a
    failed verdict as a retraction rather than a footnote. That trade-off is
    forced by the locked decision to stream everything; buffering until
    verified would trade a visible, checkable failure for a blank screen of
    up to ten minutes on the local path.
    """
    settings = get_settings()
    provider = provider or get_provider()
    k = k or settings.retrieval_k
    t0 = time.perf_counter()

    evidence = (
        await retrieve_for_session(db, session_id, question, k)
        if session_id is not None
        else await retrieve(db, question, k)
    )

    yield "sources", {
        "count": len(evidence),
        "supported": bool(evidence),
        "sources": source_summaries(evidence),
    }

    if not evidence:
        log.info("agent_abstained", extra={
            "reason": "no_evidence_above_floor",
            "provider": provider.name, "outcome": "abstained"})
        yield "delta", {"text": ABSTENTION}
        yield "grounding", verify_answer("", []).to_dict()
        yield "complete", {
            "abstained": True, "supported": False, "trustworthy": False,
            "provider": provider.name, "model": provider.model,
            "content": ABSTENTION,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }
        return

    parts: list[str] = []
    async for delta in provider.stream(
            f"{SYSTEM_PROMPT}\n\n{build_prompt(question, evidence)}"):
        parts.append(delta)
        yield "delta", {"text": delta}

    text = "".join(parts).strip()
    report = verify_answer(text, evidence)          # MANDATORY
    latency = int((time.perf_counter() - t0) * 1000)

    log.info("agent_answered", extra={
        "provider": provider.name, "model": provider.model,
        "evidence_count": len(evidence),
        "grounding_verdict": report.verdict,
        "duration_ms": latency,
        "outcome": "ok" if report.grounded else "ungrounded"})

    yield "grounding", report.to_dict()
    yield "complete", {
        "abstained": False,
        "supported": True,
        "trustworthy": report.grounded,
        "provider": provider.name,
        "model": provider.model,
        "content": text,
        "latency_ms": latency,
    }
