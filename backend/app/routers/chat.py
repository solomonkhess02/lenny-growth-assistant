"""Streaming chat (SSE).

Phase 4 replaced the Phase 2B placeholder with the real agent layer. The
event protocol grew but did not change shape: `meta` still arrives first and
exactly one terminal event still ends the stream.

Persistence goes through app.repository. This module builds no queries and
makes no retrieval or provider decisions of its own -- it is transport.

Event protocol (SSE `event:` / `data:` JSON):
  meta      - session_id, user_seq, provider, model            (always first)
  sources   - the evidence cards, sent BEFORE any text so the reader can see
              what the answer will be built from
  delta     - {"text": "..."} incremental content
  grounding - verification verdict for the completed answer
  done      - {"message_id", "seq", "latency_ms", "trustworthy", ...}
  error     - {"code", "message", "retryable"}                 (terminal)

`sources` precedes `delta` deliberately: citations are evidence the system
retrieved, not claims the model made, so they are trustworthy before a single
token is generated.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from .. import repository as repo
from ..agent import stream_answer
from ..db import get_session, session_factory
from ..errors import AppError
from ..logging_conf import request_id_var
from ..providers import ModelProvider, get_provider
from ..schemas import MessageCreate

log = logging.getLogger("app.chat")
router = APIRouter(prefix="/sessions", tags=["chat"])


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/{session_id}/messages")
async def post_message(
    session_id: uuid.UUID,
    body: MessageCreate,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    provider: ModelProvider = get_provider()

    # Persist the user turn and COMMIT before streaming. The generator below
    # runs after this handler returns and opens its own session; an
    # uncommitted row would be invisible to it -- and to the follow-up
    # retrieval that reads this session's history. Committing here also means
    # a mid-stream disconnect cannot lose the user's message.
    user_msg = await repo.append_message(
        db, session_id, role="user", content=body.content)
    user_seq = user_msg.seq
    await db.commit()

    rid = request_id_var.get()

    async def generator() -> AsyncIterator[str]:
        t0 = time.perf_counter()
        yield sse("meta", {
            "session_id": str(session_id), "user_seq": user_seq,
            "provider": provider.name, "model": provider.model,
            "request_id": rid,
        })
        try:
            final: dict = {}
            grounding: dict = {}

            async with session_factory()() as read:
                async for event, payload in stream_answer(
                        read, body.content, session_id=session_id,
                        provider=provider):
                    if await request.is_disconnected():
                        log.info("client_disconnected",
                                 extra={"session_id": str(session_id)})
                        return
                    if event == "complete":
                        final = payload
                        continue
                    if event == "grounding":
                        grounding = payload
                    yield sse(event, payload)

            content = final.get("content", "").strip()
            latency = int((time.perf_counter() - t0) * 1000)

            async with session_factory()() as write:
                msg = await repo.append_message(
                    write, session_id, role="assistant", content=content,
                    provider=provider.name, model=provider.model,
                    latency_ms=latency,
                )
                await write.commit()
                msg_id, a_seq = str(msg.id), msg.seq

            log.info("assistant_message_persisted", extra={
                "session_id": str(session_id), "seq": a_seq,
                "provider": provider.name, "model": provider.model,
                "abstained": final.get("abstained"),
                "trustworthy": final.get("trustworthy"),
                "duration_ms": latency,
                "outcome": "ok" if final.get("trustworthy") else "ungrounded"})

            yield sse("done", {
                "message_id": msg_id, "seq": a_seq, "latency_ms": latency,
                "content_length": len(content),
                "abstained": final.get("abstained", False),
                "supported": final.get("supported", False),
                "trustworthy": final.get("trustworthy", False),
                "grounding": grounding,
            })

        except AppError as exc:
            # Status is already 200 -- a mid-stream failure must be surfaced
            # as a terminal event, never swallowed into a truncated success.
            log.warning("stream_failed", extra={
                "session_id": str(session_id), "error_code": exc.code,
                "outcome": "error"})
            yield sse("error", {"code": exc.code, "message": exc.message,
                                "retryable": exc.retryable})
        except Exception:
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
