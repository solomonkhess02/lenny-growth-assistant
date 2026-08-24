"""Session and message persistence.

Session isolation is a correctness requirement, not a nicety. Every read of a
message filters on session_id; there is no code path that returns a message
without one.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..errors import NotFoundError
from ..models import Message, Session
from ..providers import get_provider
from ..schemas import SessionCreate, SessionDetail, SessionOut

log = logging.getLogger("app.sessions")
router = APIRouter(prefix="/sessions", tags=["sessions"])


async def load_session(db: AsyncSession, session_id: uuid.UUID) -> Session:
    obj = await db.get(Session, session_id)
    if obj is None:
        raise NotFoundError(f"Session {session_id} does not exist.")
    return obj


async def next_seq(db: AsyncSession, session_id: uuid.UUID) -> int:
    """Next sequence number *within this session*."""
    current = await db.scalar(
        select(func.max(Message.seq)).where(Message.session_id == session_id)
    )
    return (current or 0) + 1


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate, db: AsyncSession = Depends(get_session)
) -> Session:
    provider = get_provider()
    obj = Session(
        title=body.title,
        user_metadata=body.user_metadata or {},
        provider=provider.name,
        model=provider.model,
    )
    db.add(obj)
    await db.flush()
    log.info("session_created", extra={
        "session_id": str(obj.id), "provider": obj.provider, "model": obj.model})
    return obj


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    limit: int = 50, db: AsyncSession = Depends(get_session)
) -> list[Session]:
    rows = await db.scalars(
        select(Session).order_by(Session.created_at.desc()).limit(min(limit, 200))
    )
    return list(rows)


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session_detail(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> Session:
    obj = await load_session(db, session_id)
    # Explicit, session-scoped load. Never "all messages then filter".
    msgs = await db.scalars(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.seq)
    )
    obj.messages = list(msgs)
    return obj


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> None:
    await load_session(db, session_id)
    await db.execute(delete(Session).where(Session.id == session_id))
    log.info("session_deleted", extra={"session_id": str(session_id)})
