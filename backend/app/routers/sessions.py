"""Session and message HTTP surface.

Persistence lives in app.repository. This module translates HTTP to
repository calls and back; it builds no queries of its own.

Session isolation is structural: repository.list_messages is scoped to a
session_id by construction and has no unscoped variant.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from .. import repository as repo
from ..db import get_session
from ..errors import ValidationFailed
from ..models import Session
from ..providers import available_providers, get_provider
from ..schemas import MessageOut, SessionCreate, SessionDetail, SessionOut

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate, db: AsyncSession = Depends(get_session)
) -> Session:
    """Create a session and fix its provider for good.

    The provider is stamped here and never changes afterwards. Switching
    provider is `POST /api/sessions` again -- there is deliberately no route
    that mutates an existing session's provider.
    """
    if body.provider is not None and body.provider not in available_providers():
        # A bad name in a request BODY is the caller's mistake, so it is a 422.
        # get_provider() would raise ProviderMisconfigured (500), which is the
        # right code for a broken deployment and the wrong one for a typo.
        raise ValidationFailed(
            f"Unknown provider {body.provider!r}. "
            f"Available: {', '.join(available_providers())}.",
            field="provider",
        )

    provider = get_provider(body.provider)
    return await repo.create_session(
        db, title=body.title, user_metadata=body.user_metadata,
        provider=provider.name, model=provider.model,
    )


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_session),
) -> list[Session]:
    return await repo.list_sessions(db, limit=limit)


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session_detail(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> SessionDetail:
    obj = await repo.load_session(db, session_id)
    msgs = await repo.list_messages(db, session_id)
    # Build the response explicitly rather than assigning to obj.messages,
    # which would overwrite a live SQLAlchemy relationship.
    return SessionDetail(
        **SessionOut.model_validate(obj).model_dump(),
        messages=[MessageOut.model_validate(m) for m in msgs],
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> None:
    await repo.delete_session(db, session_id)


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> list:
    await repo.load_session(db, session_id)
    return await repo.list_messages(db, session_id)
