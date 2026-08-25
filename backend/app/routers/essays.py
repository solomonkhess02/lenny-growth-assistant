"""Ship 30 essay generation (SSE) and retrieval.

Transport only, like routers/chat.py: this module builds no queries, makes no
retrieval decisions, and chooses no provider. It translates HTTP to
repository/ship30 calls and frames the result as SSE.

Event protocol -- deliberately IDENTICAL to the chat protocol, because the
essay inherits the same locked Provider UX contract rather than inventing a
second one:

  meta      - session_id, provider, model, skill, skill_sha256  (always first)
  sources   - the evidence cards, sent BEFORE any text
  delta     - {"text": "..."} incremental Markdown
  grounding - verification verdict for the finished essay
  done      - {"essay_id", "word_count", "within_target", "trustworthy", ...}
  error     - {"code", "message", "retryable", "request_id"}     (terminal)

The entry conditions below are enforced HERE and not only in the UI. A hidden
button is not an access control, and the rule that matters most -- no essay is
written from an answer that failed verification -- has to hold for any caller.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from .. import artifacts
from .. import repository as repo
from ..db import db_errors, get_session, session_factory
from ..errors import AppError, NotFoundError, ResourceConflict, ValidationFailed
from ..logging_conf import request_id_var
from ..models import Essay, Message
from ..providers import ModelProvider, get_provider
from ..schemas import EssayCreate, EssayOut
from ..ship30 import load_skill, stream_essay

log = logging.getLogger("app.essays")

router = APIRouter(tags=["essays"])


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _source_answer(db: AsyncSession, session_id: uuid.UUID,
                         message_id: uuid.UUID) -> tuple[Message, str]:
    """Load the answer to write from, and the question that produced it.

    Returns (assistant_message, question). Raises rather than returning a
    partial result: every rejection below is a case where writing 1,250 words
    would produce something the reader should not be given.
    """
    messages = await repo.list_messages(db, session_id)
    by_id = {m.id: m for m in messages}

    # A message from another session is reported as absent, not forbidden: it
    # does not exist *for this session*, and saying otherwise would confirm
    # that some other session holds it.
    answer = by_id.get(message_id)
    if answer is None:
        raise NotFoundError(
            f"Message {message_id} does not exist in this session.")

    if answer.role != "assistant":
        raise ValidationFailed(
            "An essay is written from an assistant answer, not from a "
            f"{answer.role!r} turn.",
            field="source_message_id")

    if not answer.sources:
        # The abstention path: no evidence was found, so the model was never
        # invoked. There is nothing to write an essay from, and generating one
        # anyway would mean writing from the model's own memory.
        raise ValidationFailed(
            "That turn cited no evidence, so there is nothing to write an "
            "essay from. It is an abstention: the transcripts did not support "
            "the question.",
            field="source_message_id")

    # The single most important guard here. Building 1,250 words on an answer
    # already known to contain fabricated quotes or invalid citations would
    # launder a failure into a longer, more confident artifact.
    #
    # A missing verdict is refused on the same footing as a failed one: NULL
    # means nothing was recorded, which is not the same claim as a recorded
    # PASS, and this is exactly the place that distinction has to hold.
    verdict = answer.grounding or {}
    if not verdict.get("grounded"):
        raise ResourceConflict(
            "That answer did not pass verification, so it cannot be turned "
            "into an essay. Ask the question again and write from an answer "
            "that verifies clean."
            if verdict
            else "That answer has no recorded verification verdict, so it "
                 "cannot be turned into an essay."
        )

    # The question is the user turn immediately preceding the answer. Read from
    # the same session-scoped list, so it cannot come from anywhere else.
    question = next(
        (m.content for m in reversed(messages)
         if m.role == "user" and m.seq < answer.seq), "")
    return answer, question


@router.post("/sessions/{session_id}/essays")
async def create_essay(
    session_id: uuid.UUID,
    body: EssayCreate,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    # The SESSION decides the provider -- never LLM_PROVIDER, never the request
    # body. An essay therefore always runs on the same model as the answer it
    # is written from, because both read this one immutable row.
    session = await repo.load_session(db, session_id)
    provider: ModelProvider = get_provider(session.provider)

    answer, question = await _source_answer(db, session_id, body.source_message_id)
    # Read before the stream opens: a missing skill file is a misconfiguration
    # that should fail as a clean HTTP error, not as a terminal event three
    # minutes into a generation.
    skill_name, _, skill_sha = load_skill()

    # Copied out now: the generator below runs after this handler returns and
    # this session is closed by then, so touching an ORM instance there would
    # raise on a lazy refresh.
    answer_id, answer_text = answer.id, answer.content
    stored_sources = list(answer.sources or [])
    rid = request_id_var.get()

    async def generator() -> AsyncIterator[str]:
        t0 = time.perf_counter()
        yield sse("meta", {
            "session_id": str(session_id),
            "source_message_id": str(answer_id),
            "provider": provider.name, "model": provider.model,
            "skill": skill_name, "skill_sha256": skill_sha,
            "kind": "essay",
            "request_id": rid,
        })
        try:
            final: dict = {}
            grounding: dict = {}
            sources: list[dict] = []

            async with (
                session_factory()() as read,
                db_errors(),
                # Deterministic teardown: see the identical comment in
                # routers/chat.py. Without it, closing depended on the event
                # loop's async-generator finalizer, and an abandoned essay
                # kept its Pi subprocess (and cloud token spend) running
                # unobserved until GC happened to collect it.
                aclosing(stream_essay(
                    read, question=question, answer=answer_text,
                    stored_sources=stored_sources, provider=provider),
                ) as essay_stream,
            ):
                async for event, payload in essay_stream:
                    if await request.is_disconnected():
                        # A partial essay is not an essay: nothing is persisted.
                        log.info("client_disconnected", extra={
                            "session_id": str(session_id), "kind": "essay",
                            "outcome": "abandoned"})
                        return
                    if event == "complete":
                        final = payload
                        continue
                    if event == "grounding":
                        grounding = payload
                    if event == "sources":
                        sources = payload.get("sources", [])
                    yield sse(event, payload)

            markdown = final.get("markdown", "").strip()
            latency = int((time.perf_counter() - t0) * 1000)

            async with session_factory()() as write, db_errors():
                essay = await repo.create_essay(
                    write, session_id,
                    source_message_id=answer_id,
                    title=final.get("title"),
                    markdown=markdown,
                    word_count=final.get("word_count", 0),
                    provider=provider.name, model=provider.model,
                    latency_ms=latency,
                    # Stored so a reopened essay still shows what it was written
                    # from -- and still shows a FAILED verdict as a retraction.
                    sources=sources, grounding=grounding or None,
                    skill_name=skill_name, skill_sha256=skill_sha,
                )
                await write.commit()
                essay_id = str(essay.id)

            log.info("essay_persisted", extra={
                "session_id": str(session_id), "essay_id": essay_id,
                "provider": provider.name, "model": provider.model,
                "skill": skill_name, "skill_sha256": skill_sha,
                "word_count": final.get("word_count"),
                "within_target": final.get("within_target"),
                "trustworthy": final.get("trustworthy"),
                "duration_ms": latency,
                "outcome": "ok" if final.get("trustworthy") else "ungrounded"})

            yield sse("done", {
                "essay_id": essay_id,
                "title": final.get("title"),
                "word_count": final.get("word_count", 0),
                "target_words": final.get("target_words"),
                "within_target": final.get("within_target", False),
                "blockquote_lines": final.get("blockquote_lines", 0),
                "trustworthy": final.get("trustworthy", False),
                "supported": final.get("supported", False),
                "latency_ms": latency,
                "grounding": grounding,
            })

        except AppError as exc:
            # Status is already 200 -- a mid-stream failure is surfaced as a
            # terminal event rather than swallowed into a truncated success.
            # Nothing is retried and no provider is substituted.
            log.warning("essay_stream_failed", extra={
                "session_id": str(session_id), "error_code": exc.code,
                "outcome": "error"})
            yield sse("error", {"code": exc.code, "message": exc.message,
                                "retryable": exc.retryable, "request_id": rid})
        except asyncio.CancelledError:
            # See the identical handler in routers/chat.py: the transport is
            # already gone by the time this fires, so nothing can be sent --
            # this only logs (never silently) and re-raises.
            log.info("essay_stream_cancelled", extra={
                "session_id": str(session_id), "outcome": "cancelled"})
            raise
        except Exception:
            log.exception("essay_stream_unhandled", extra={
                "session_id": str(session_id), "outcome": "error"})
            yield sse("error", {"code": "internal_error",
                                "message": "An unexpected error occurred.",
                                "retryable": False, "request_id": rid})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions/{session_id}/essays", response_model=list[EssayOut])
async def list_session_essays(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> list[Essay]:
    await repo.load_session(db, session_id)
    return await repo.list_essays(db, session_id)


@router.get("/essays/{essay_id}", response_model=EssayOut)
async def get_essay(
    essay_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> Essay:
    return await repo.load_essay(db, essay_id)


@router.get("/essays/{essay_id}/render")
async def render_essay(
    essay_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> dict:
    """The Phase 7 boundary: turn a stored essay's Markdown into sanitized,
    isolatable HTML for the Artifact Pane's sandboxed iframe.

    JSON, not `text/html` -- an HTML response would be navigable at its own
    same-origin URL, which is the one response shape that could turn a
    sanitizer miss into stored XSS rather than an inert JSON string.

    Two distinct "not rendered" outcomes, deliberately not collapsed:

      - A retracted essay returns 200 with `rendered: false` and a reason.
        This is the policy working, not a failure -- the same distinction
        Phase 5 draws between `retracted` and `error` for a chat turn.
        Rendering is never even attempted, so a fabrication gets no chance
        at a "safely sanitized" rich render.
      - `artifacts.render()` raising is an actual refusal (oversized input,
        unsupported format, a sanitizer fault, or the post-render safety
        re-scan tripping) and surfaces as the structured error envelope via
        the registered `AppError` handler, which also logs it.
    """
    essay = await repo.load_essay(db, essay_id)

    grounded = bool(essay.grounding and essay.grounding.get("grounded"))
    if not grounded:
        reason = "retracted" if essay.grounding else "ungrounded"
        log.info("artifact_render_skipped", extra={
            "essay_id": str(essay_id), "reason": reason})
        return {
            "essay_id": str(essay_id),
            "rendered": False,
            "reason": reason,
            "policy_version": artifacts.POLICY_VERSION,
        }

    result = artifacts.render(essay.markdown, format=essay.format)
    log.info("artifact_rendered", extra={
        "essay_id": str(essay_id), "format": essay.format,
        "blocked": result.blocked, "stripped": result.stripped,
        "policy_version": result.policy_version})
    return {
        "essay_id": str(essay_id),
        "rendered": True,
        "html": result.html,
        "blocked": result.blocked,
        "stripped": result.stripped,
        "policy_version": result.policy_version,
    }
