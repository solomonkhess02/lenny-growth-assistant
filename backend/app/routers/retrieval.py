"""Read-only retrieval surface.

Exists so the retrieval layer can be verified and demonstrated without a
model in the loop, and because Phase 4's agent will call exactly this path.
Keeping it a real endpoint means the thing evaluated is the thing shipped.

Read-only by construction: it issues SELECTs and returns evidence. There is no
write path here.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..models import Chunk, Transcript
from ..retrieval import UNSET, index_status, retrieve, retrieve_for_session

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, max_length=1000,
                   description="The question to find evidence for."),
    k: int | None = Query(None, ge=1, le=20),
    session_id: uuid.UUID | None = Query(
        None, description="Resolve follow-ups against this session's history. "
                          "Scoped to this session only."),
    min_similarity: float | None = Query(None, ge=-1.0, le=1.0),
    db: AsyncSession = Depends(get_session),
) -> dict:
    settings = get_settings()
    k = k or settings.retrieval_k

    floor = UNSET if min_similarity is None else min_similarity
    if session_id is not None:
        evidence = await retrieve_for_session(
            db, session_id, q, k, min_similarity=floor)
    else:
        evidence = await retrieve(db, q, k, min_similarity=floor)

    return {
        "query": q,
        "session_id": str(session_id) if session_id else None,
        "count": len(evidence),
        # An empty result is a real answer, not an error: it means the
        # transcript material does not support the question.
        "supported": bool(evidence),
        "min_similarity": (settings.retrieval_min_similarity
                           if min_similarity is None else min_similarity),
        "embedding_model": settings.embedding_model,
        "evidence": [e.to_dict() for e in evidence],
    }


@router.get("/status")
async def status(db: AsyncSession = Depends(get_session)) -> dict:
    """What is actually indexed. Makes an uningested corpus obvious."""
    settings = get_settings()
    transcripts = await db.scalar(select(func.count()).select_from(Transcript))
    chunks = await db.scalar(select(func.count()).select_from(Chunk))
    idx = await index_status(db)

    configured_matches = (
        not idx["populated"]
        or (idx["models"] == [settings.embedding_model]
            and idx["dims"] == [settings.embedding_dim])
    )
    return {
        "transcripts": transcripts or 0,
        "chunks": chunks or 0,
        "index_embedding_models": idx["models"],
        "index_embedding_dims": idx["dims"],
        "configured_embedding_model": settings.embedding_model,
        "configured_embedding_dim": settings.embedding_dim,
        # False means retrieval will refuse rather than silently compare
        # vectors from two different models.
        "compatible": configured_matches,
        "retrieval_k": settings.retrieval_k,
        "retrieval_min_similarity": settings.retrieval_min_similarity,
        "retrieval_max_per_source": settings.retrieval_max_per_source,
    }
