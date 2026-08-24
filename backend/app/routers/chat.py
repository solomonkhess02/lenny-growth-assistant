"""Streaming plumbing (SSE).

Requirement 1 of the locked Provider UX contract makes streaming baseline UX
for every model request, so the transport is built into the skeleton rather
than retrofitted in Phase 5.

Phase 2B streams a deterministic placeholder: no model is involved, so the
skeleton runs and its tests pass without Ollama. Phase 4 replaces
`_placeholder_stream` with the agent; the event protocol below does not change.

Event protocol (SSE `event:` / `data:` JSON):
  meta   - session_id, seq, provider, model            (always first)
  delta  - {"text": "..."} incremental content
  done   - {"message_id", "seq", "latency_ms", "content_length"}
  error  - {"code", "message", "retryable"}            (terminal)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session, session_factory
from ..errors import AppError
from ..models import Message
from ..providers import ModelProvider, get_provider
from ..schemas import MessageCreate, MessageOut
from ..logging_conf import request_id_var
from .sessions import load_session, next_seq

log = logging.getLogger("app.chat")
router = APIRouter(prefix="/sessions", tags=["chat"])

PLACEHOLDER_NOTICE = (
    "[Phase 2B skeleton] Retrieval and generation are not wired yet. "
    "This response is a deterministic placeholder that exercises the "
    "streaming transport, session persistence, and provider seam. "
    "You said: "
)


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _placeholder_stream(prompt: str) -> AsyncIterator[str]:
    """Deterministic, model-free. Chunked so incremental delivery is provable."""
    for word in (PLACEHOLDER_NOTICE + prompt).split(" "):
        yield word + " "
        await asyncio.sleep(0.01)


@router.post("/{session_id}/messages")
async def post_message(
    session_id: uuid.UUID,
    body: MessageCreate,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    # Validate and persist the user turn *before* streaming: if the client
    # disconnects mid-stream, their message is not lost.
    session = await load_session(db, session_id)
    provider: ModelProvider = get_provider()

    seq = await next_seq(db, session_id)
    db.add(Message(session_id=session_id, seq=seq, role="user",
                   content=body.content))
    # COMMIT, not flush. The streaming generator below runs after this handler
    # returns and opens its own session to allocate the assistant's seq. An
    # uncommitted user row is invisible to that session, so it would reuse the
    # same seq and then block on UNIQUE(session_id, seq) waiting for a
    # transaction that only commits once the stream ends -- a self-deadlock.
    # Committing here also honours the stated intent: the user's turn survives
    # a mid-stream disconnect.
    await db.commit()
    user_seq = seq
    rid = request_id_var.get()

    async def generator() -> AsyncIterator[str]:
        t0 = time.perf_counter()
        parts: list[str] = []
        yield sse("meta", {
            "session_id": str(session_id), "user_seq": user_seq,
            "provider": provider.name, "model": provider.model,
            "request_id": rid, "phase": "2B-placeholder",
        })
        try:
            async for delta in _placeholder_stream(body.content):
                if await request.is_disconnected():
                    log.info("client_disconnected", extra={
                        "session_id": str(session_id)})
                    break
                parts.append(delta)
                yield sse("delta", {"text": delta})

            content = "".join(parts).strip()
            latency = int((time.perf_counter() - t0) * 1000)

            # Own session: the request-scoped one may already be closed by the
            # time a streaming body finishes.
            async with session_factory()() as write:
                a_seq = await next_seq(write, session_id)
                msg = Message(
                    session_id=session_id, seq=a_seq, role="assistant",
                    content=content, provider=provider.name,
                    model=provider.model, latency_ms=latency,
                )
                write.add(msg)
                await write.commit()
                await write.refresh(msg)
                msg_id, a_seq = str(msg.id), msg.seq

            log.info("assistant_message_persisted", extra={
                "session_id": str(session_id), "seq": a_seq,
                "provider": provider.name, "model": provider.model,
                "duration_ms": latency, "outcome": "ok"})

            yield sse("done", {"message_id": msg_id, "seq": a_seq,
                               "latency_ms": latency,
                               "content_length": len(content)})

        except AppError as exc:
            # Status is already 200 — a mid-stream failure must be surfaced as
            # a terminal event, never swallowed into a truncated success.
            log.warning("stream_failed", extra={
                "session_id": str(session_id), "error_code": exc.code,
                "outcome": "error"})
            yield sse("error", {"code": exc.code, "message": exc.message,
                                "retryable": exc.retryable})
        except Exception as exc:  # noqa: BLE001
            log.exception("stream_unhandled", extra={
                "session_id": str(session_id), "outcome": "error"})
            yield sse("error", {"code": "internal_error",
                                "message": "An unexpected error occurred.",
                                "retryable": False})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # defeat proxy buffering
        },
    )


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> list[Message]:
    from sqlalchemy import select
    await load_session(db, session_id)
    rows = await db.scalars(
        select(Message).where(Message.session_id == session_id)
        .order_by(Message.seq)
    )
    return list(rows)
