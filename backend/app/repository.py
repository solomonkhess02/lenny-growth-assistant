"""Persistence layer for conversation state.

Skill 03 requires distinct layers: API -> session -> ... -> persistence.
Before this module, routers *were* the persistence layer, and
`routers/chat.py` imported data access from `routers/sessions.py`. That
coupling is what made two bugs easy to introduce:

  - the Phase 2B streaming self-deadlock (seq allocated in one module,
    committed in another), and
  - the H1 concurrency race (read-then-insert with no lock).

Both stemmed from sequence allocation living outside the write that
depends on it. Here, allocation and insertion are a single operation that
no caller can take apart.

Routers call these functions; they do not build queries. Transaction
boundaries stay with the caller so a request can group several writes.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .errors import NotFoundError
from .models import Message, Session

log = logging.getLogger("app.repository")

# Mirrors ck_messages_role in the database. A bare `str` alias would be
# an identity wrapper; Literal actually rejects a bad role at type-check.
Role = Literal["user", "assistant", "system"]


async def create_session(
    db: AsyncSession, *, title: str | None, user_metadata: dict[str, Any],
    provider: str, model: str,
) -> Session:
    obj = Session(title=title, user_metadata=user_metadata or {},
                  provider=provider, model=model)
    db.add(obj)
    await db.flush()
    log.info("session_created", extra={
        "session_id": str(obj.id), "provider": provider, "model": model})
    return obj


async def load_session(db: AsyncSession, session_id: uuid.UUID) -> Session:
    obj = await db.get(Session, session_id)
    if obj is None:
        raise NotFoundError(f"Session {session_id} does not exist.")
    return obj


async def list_sessions(db: AsyncSession, limit: int = 50) -> list[Session]:
    rows = await db.scalars(
        select(Session).order_by(Session.created_at.desc()).limit(limit))
    return list(rows)


async def list_messages(db: AsyncSession, session_id: uuid.UUID) -> list[Message]:
    """Session-scoped by construction. There is no unscoped variant."""
    rows = await db.scalars(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.seq)
    )
    return list(rows)


async def delete_session(db: AsyncSession, session_id: uuid.UUID) -> None:
    await load_session(db, session_id)
    await db.execute(delete(Session).where(Session.id == session_id))
    log.info("session_deleted", extra={"session_id": str(session_id)})


async def append_message(
    db: AsyncSession, session_id: uuid.UUID, *, role: Role, content: str,
    provider: str | None = None, model: str | None = None,
    latency_ms: int | None = None,
) -> Message:
    """Append a turn, allocating its sequence number atomically.

    Concurrency: the parent `sessions` row is locked FOR UPDATE before
    MAX(seq) is read, so concurrent appends to the SAME session serialise
    on that row while appends to *different* sessions stay fully parallel.

    Without the lock, READ COMMITTED lets two transactions read the same
    MAX(seq) and both insert seq+1; one then dies on
    UNIQUE(session_id, seq). Measured before this change: 6 concurrent
    posts to one session -> 4 failures.

    The lock is held until the caller commits, which is also what makes
    allocation and insertion inseparable.
    """
    locked = await db.scalar(
        select(Session.id).where(Session.id == session_id).with_for_update()
    )
    if locked is None:
        # Checked under the same lock as the insert, so a session deleted
        # concurrently cannot slip a message in behind it.
        raise NotFoundError(f"Session {session_id} does not exist.")

    current = await db.scalar(
        select(func.max(Message.seq)).where(Message.session_id == session_id))
    seq = (current or 0) + 1

    msg = Message(
        session_id=session_id, seq=seq, role=role, content=content,
        provider=provider, model=model, latency_ms=latency_ms,
    )
    db.add(msg)
    await db.flush()
    return msg
