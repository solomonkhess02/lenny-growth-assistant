"""Deterministic retrieval over the transcript index.

This is ordinary application code, NOT an agent tool. Skill 03 forbids using
an agent for deterministic operations, and Phase 1 measured the practical
reason: each tool round trip costs ~24s locally. Retrieval is a database
query; it should behave like one.

Determinism is a correctness property here, not a nicety. The same question
must produce the same evidence every time, or the evaluation suite measures
noise and a user cannot reproduce what they saw. Two things guarantee it:

  1. Exact search -- `ORDER BY embedding <=> query`. No ANN index, so recall
     is 100% by construction. At 1,395 chunks pgvector answers in low
     single-digit milliseconds against a 25-620s generation step.
  2. A total tie-break -- (distance, transcript_id, chunk_index). Equal
     distances can otherwise reorder between runs at the database's
     discretion.

Nothing here fabricates source information. Every field on an Evidence object
is read from the row the vector came from, so a citation is a foreign key
rather than a claim.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .embeddings import EmbeddingClient
from .errors import EvidenceUnavailable, ProviderMisconfigured
from .models import Chunk, Transcript
from .repository import list_messages

log = logging.getLogger("app.retrieval")

# The locked Provider UX contract ties prompt size to local latency: Phase 1
# measured 30 of Test A's 48s as prompt processing at ~118 tok/s prefill. Three
# tight chunks is the retrieval-side consequence of that measurement.
DEFAULT_K = 3


class _Unset:
    """Sentinel distinguishing 'caller said nothing' from 'caller said None'.

    `max_per_source=None` legitimately means "no cap at all", so None cannot
    also mean "fall back to the configured default" -- that overload made the
    cap impossible to disable, and would have silently applied the default cap
    to a calibration re-run that asked for no cap.
    """

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "<unset>"


UNSET = _Unset()

# The floor and per-source cap live in Settings so they are configuration,
# not constants buried in a module. Both were set by the pre-registered
# calibration -- see docs/retrieval-calibration.md.


@dataclass(frozen=True)
class Evidence:
    """One citable span. Every field comes from the stored row."""

    # --- skill 02 metadata contract ---
    source_id: str            # transcripts.slug
    source_title: str         # transcripts.title
    speaker: str              # chunks.speaker
    source_url: str           # transcripts.youtube_url
    transcript_id: str        # chunks.transcript_id
    chunk_id: str             # chunks.id
    publish_date: date | None  # transcripts.publish_date

    # --- attribution + ranking ---
    chunk_index: int
    guest: str
    text: str
    start_seconds: int
    end_seconds: int
    similarity: float
    citation_url: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["publish_date"] = self.publish_date.isoformat() if self.publish_date else None
        return d


def _citation_url(youtube_url: str, start_seconds: int) -> str:
    """Deep-link to the exact moment, so a human can verify the citation.

    This is the feature that makes 'no fabricated quotes' checkable by the
    reader rather than merely asserted by us.
    """
    if not youtube_url:
        return ""
    sep = "&" if "?" in youtube_url else "?"
    return f"{youtube_url}{sep}t={max(0, int(start_seconds))}"


async def index_status(db: AsyncSession) -> dict:
    """What the index actually contains. Used by the guard and by /search."""
    rows = (await db.execute(
        select(Chunk.embedding_model, Chunk.embedding_dim)
        .distinct()
    )).all()
    # No second query: zero distinct (model, dim) pairs IS an empty index.
    return {
        "models": sorted({r[0] for r in rows}),
        "dims": sorted({r[1] for r in rows}),
        "populated": bool(rows),
    }


async def _assert_compatible(db: AsyncSession, embedder: EmbeddingClient) -> None:
    """Refuse to search an index built by a different embedding model.

    This guard is load-bearing rather than defensive. The `embedding` column
    is deliberately dimensionless (so switching models needs no migration),
    which means PostgreSQL will happily store 384- and 768-wide vectors side
    by side and compare them as if they meant the same thing. Verified against
    the live server. Nothing below the application layer can catch this.
    """
    status = await index_status(db)
    if not status["populated"]:
        return

    if status["models"] != [embedder.model] or status["dims"] != [embedder.dim]:
        raise ProviderMisconfigured(
            f"Embedding model mismatch: the index was built with "
            f"{status['models']} at dimensions {status['dims']}, but this "
            f"deployment is configured for '{embedder.model}' at "
            f"{embedder.dim}. Comparing vectors across models is meaningless "
            f"and would silently return wrong evidence. Either set "
            f"EMBEDDING_MODEL back, or re-ingest: python -m app.ingest --force"
        )


async def retrieve(
    db: AsyncSession,
    query: str,
    k: int = DEFAULT_K,
    *,
    min_similarity: float | _Unset | None = UNSET,
    max_per_source: int | _Unset | None = UNSET,
    embedder: EmbeddingClient | None = None,
) -> list[Evidence]:
    """Return up to `k` pieces of evidence, or [] when nothing clears the floor.

    An empty result is a legitimate, meaningful answer: it is how the system
    says the transcript material does not support the question.
    """
    settings = get_settings()
    embedder = embedder or EmbeddingClient()
    floor = (settings.retrieval_min_similarity
             if isinstance(min_similarity, _Unset) else min_similarity)
    cap = (settings.retrieval_max_per_source
           if isinstance(max_per_source, _Unset) else max_per_source)

    if not query or not query.strip():
        return []

    await _assert_compatible(db, embedder)

    t0 = time.perf_counter()
    qvec = await embedder.embed_one(query)

    distance = Chunk.embedding.cosine_distance(qvec).label("distance")
    # Over-fetch so the per-source cap still has candidates to fall back on.
    fetch = k * 6 if cap else k
    stmt = (
        select(Chunk, Transcript, distance)
        .join(Transcript, Transcript.id == Chunk.transcript_id)
        # Total ordering. Without the trailing keys, equal distances may
        # reorder between runs and retrieval stops being reproducible.
        .order_by(distance, Chunk.transcript_id, Chunk.chunk_index)
        .limit(fetch)
    )

    per_source: dict[uuid.UUID, int] = {}
    out: list[Evidence] = []
    for chunk, transcript, dist in (await db.execute(stmt)).all():
        similarity = 1.0 - float(dist)
        if similarity < floor:
            # Ordered by distance, so everything after this is worse too.
            break
        if cap and per_source.get(chunk.transcript_id, 0) >= cap:
            continue
        per_source[chunk.transcript_id] = per_source.get(chunk.transcript_id, 0) + 1
        out.append(_evidence_from_row(chunk, transcript, round(similarity, 6)))
        if len(out) >= k:
            break

    log.info("retrieval", extra={
        "query_chars": len(query), "k": k, "returned": len(out),
        "floor": floor, "max_per_source": cap,
        "sources": sorted({e.source_id for e in out}),
        "top_similarity": out[0].similarity if out else None,
        "embedding_model": embedder.model,
        "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
        "outcome": "ok" if out else "empty",
    })
    return out


def _evidence_from_row(chunk: Chunk, transcript: Transcript,
                       similarity: float) -> Evidence:
    """Build an Evidence from the two rows it lives in.

    Shared by the vector search and by rehydration so the two can never drift
    into describing the same chunk differently.
    """
    return Evidence(
        source_id=transcript.slug,
        source_title=transcript.title,
        speaker=chunk.speaker,
        source_url=transcript.youtube_url,
        transcript_id=str(chunk.transcript_id),
        chunk_id=str(chunk.id),
        publish_date=transcript.publish_date,
        chunk_index=chunk.chunk_index,
        guest=transcript.guest,
        text=chunk.text,
        start_seconds=chunk.start_seconds,
        end_seconds=chunk.end_seconds,
        similarity=similarity,
        citation_url=_citation_url(transcript.youtube_url, chunk.start_seconds),
    )


async def evidence_by_chunk_ids(
    db: AsyncSession,
    chunk_ids: list[str],
    *,
    similarities: dict[str, float] | None = None,
) -> list[Evidence]:
    """Re-read specific chunks by primary key, in the order asked for.

    This is NOT a search. It is how a stored turn gets its evidence back: the
    citation cards on a persisted message carry the chunk ids, and reading
    those rows reproduces exactly what the model was shown -- no embedding, no
    ranking, no floor, nothing that could return different material than the
    reader already saw.

    Order is the caller's, not the database's, because the caller's order is
    what [E1], [E2]... already mean to a reader looking at that turn.

    `similarities` restores the score each chunk had when it was retrieved.
    Recomputing it is impossible without the original query and inventing one
    would put a number on a card that nothing measured, so an unknown score is
    left at 0.0 rather than guessed.

    Raises EvidenceUnavailable if ANY id is missing. Partial evidence is not a
    smaller version of the same answer -- it is different evidence under the
    same labels.
    """
    if not chunk_ids:
        return []

    wanted: list[uuid.UUID] = []
    for cid in chunk_ids:
        try:
            wanted.append(uuid.UUID(str(cid)))
        except (ValueError, AttributeError, TypeError) as exc:
            raise EvidenceUnavailable(
                f"Stored evidence carries a malformed chunk id ({cid!r})."
            ) from exc

    rows = (await db.execute(
        select(Chunk, Transcript)
        .join(Transcript, Transcript.id == Chunk.transcript_id)
        .where(Chunk.id.in_(wanted))
    )).all()
    by_id = {str(chunk.id): (chunk, transcript) for chunk, transcript in rows}

    missing = [str(c) for c in wanted if str(c) not in by_id]
    if missing:
        raise EvidenceUnavailable(
            f"{len(missing)} of {len(wanted)} cited chunks no longer exist "
            f"(first: {missing[0]}). The corpus was most likely re-ingested "
            f"since that answer was written -- `python -m app.ingest --force` "
            f"replaces chunk ids. Ask the question again to get a fresh answer "
            f"to write from."
        )

    scores = similarities or {}
    out = [
        _evidence_from_row(*by_id[str(cid)], float(scores.get(str(cid), 0.0)))
        for cid in wanted
    ]

    log.info("evidence_rehydrated", extra={
        "requested": len(wanted), "returned": len(out),
        "sources": sorted({e.source_id for e in out}),
        "outcome": "ok",
    })
    return out


async def retrieve_for_session(
    db: AsyncSession,
    session_id: uuid.UUID,
    query: str,
    k: int = DEFAULT_K,
    *,
    history_turns: int = 2,
    **kwargs,
) -> list[Evidence]:
    """Retrieve with follow-up context drawn from THIS session only.

    A follow-up like "why does that work?" carries almost no retrievable
    signal alone. Prefixing the session's recent user turns resolves the
    pronoun against what was actually being discussed.

    Session isolation is structural, not policed here: the history comes from
    `repository.list_messages`, which is scoped to a session_id by
    construction and has no unscoped variant. There is no code path by which
    another session's turns could reach this query.
    """
    context = ""
    if history_turns > 0:
        messages = await list_messages(db, session_id)
        prior = [m.content for m in messages if m.role == "user"][-history_turns:]
        if prior:
            context = " ".join(prior) + " "

    return await retrieve(db, f"{context}{query}".strip(), k, **kwargs)
