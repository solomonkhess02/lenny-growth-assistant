"""The ingestion pipeline: fetch -> parse -> chunk -> embed -> store.

Two properties matter more than speed here.

**Nothing is ever partially ingested.** Each episode is one transaction. If
parsing, embedding or writing fails at any point, that episode's transaction
rolls back completely and the failure is reported. There is no state where an
episode is half in the index, which would silently make some of its content
unfindable while the episode still looks present.

**Nothing is ever silently skipped.** A skip happens for exactly one reason
(content hash AND embedding model both unchanged), it is reported as a skip,
and every other outcome is a success or a named failure. The Phase 1 defect --
an episode contributing zero content with no error -- is now structurally
impossible: the parser raises on zero turns and the database CHECK refuses
turn_count <= 0.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_factory
from ..embeddings import EmbeddingClient
from ..errors import ValidationFailed
from ..models import Chunk, Transcript
from .chunk import chunk_turns
from .fetch import ensure_local, load_manifest
from .parse import merge_backchannel, parse_transcript

log = logging.getLogger("app.ingest")


@dataclass
class IngestResult:
    slug: str
    status: str  # "ingested" | "skipped" | "failed"
    chunks: int = 0
    turns: int = 0
    words: int = 0
    duration_ms: float = 0.0
    error: str | None = None


@dataclass
class IngestReport:
    results: list[IngestResult] = field(default_factory=list)

    @property
    def ingested(self) -> list[IngestResult]:
        return [r for r in self.results if r.status == "ingested"]

    @property
    def skipped(self) -> list[IngestResult]:
        return [r for r in self.results if r.status == "skipped"]

    @property
    def failed(self) -> list[IngestResult]:
        return [r for r in self.results if r.status == "failed"]

    @property
    def total_chunks(self) -> int:
        return sum(r.chunks for r in self.results)

    @property
    def ok(self) -> bool:
        return not self.failed


async def _existing(db: AsyncSession, slug: str) -> Transcript | None:
    return await db.scalar(select(Transcript).where(Transcript.slug == slug))


async def _chunk_count(db: AsyncSession, transcript_id) -> int:
    return await db.scalar(
        select(func.count()).select_from(Chunk)
        .where(Chunk.transcript_id == transcript_id)) or 0


async def ingest_one(
    slug: str,
    expected_sha256: str,
    embedder: EmbeddingClient,
    *,
    force: bool = False,
) -> IngestResult:
    """Ingest a single episode atomically. Never raises; reports failure."""
    t0 = time.perf_counter()

    try:
        raw = await ensure_local(slug, expected_sha256)
        transcript = parse_transcript(slug, raw)   # raises on zero turns

        async with session_factory()() as db:
            prior = await _existing(db, slug)
            unchanged = (
                prior is not None
                and prior.content_hash == transcript.content_hash
                and prior.embedding_model == embedder.model
                and prior.embedding_dim == embedder.dim
            )
            if unchanged and not force:
                # The ONLY legitimate skip -- and it deliberately requires the
                # embedding model to match. The same text embedded by a
                # different model is a different vector space, not a cache hit.
                return IngestResult(
                    slug, "skipped",
                    chunks=await _chunk_count(db, prior.id),
                    turns=transcript.turn_count,
                    words=transcript.word_count,
                    duration_ms=(time.perf_counter() - t0) * 1000)

            chunks = chunk_turns(merge_backchannel(transcript.turns))
            if not chunks:
                raise ValidationFailed(
                    f"Transcript '{slug}' produced {transcript.turn_count} "
                    f"turns but zero chunks. Refusing to ingest an episode "
                    f"that would contribute nothing retrievable.")

            # Embed BEFORE touching the database. If Ollama is unavailable we
            # fail without having deleted the previous good copy of this
            # episode -- a failed refresh must not leave a hole in the corpus.
            vectors = await embedder.embed([c.text for c in chunks])

            # Replace in one transaction; the delete cascades to chunks.
            if prior is not None:
                await db.execute(
                    delete(Transcript).where(Transcript.id == prior.id))

            row = Transcript(
                slug=transcript.slug,
                guest=transcript.guest,
                title=transcript.title,
                youtube_url=transcript.youtube_url,
                video_id=transcript.video_id,
                publish_date=transcript.publish_date,
                channel=transcript.channel,
                keywords=transcript.keywords,
                content_hash=transcript.content_hash,
                word_count=transcript.word_count,
                turn_count=transcript.turn_count,
                embedding_model=embedder.model,
                embedding_dim=embedder.dim,
            )
            db.add(row)
            await db.flush()

            db.add_all([
                Chunk(
                    transcript_id=row.id,
                    chunk_index=c.chunk_index,
                    speaker=c.speaker,
                    text=c.text,
                    start_seconds=c.start_seconds,
                    end_seconds=c.end_seconds,
                    token_estimate=c.token_estimate,
                    embedding=vec,
                    embedding_model=embedder.model,
                    embedding_dim=embedder.dim,
                )
                for c, vec in zip(chunks, vectors)
            ])
            await db.commit()

        ms = (time.perf_counter() - t0) * 1000
        log.info("transcript_ingested", extra={
            "slug": slug, "chunks": len(chunks),
            "turns": transcript.turn_count,
            "embedding_model": embedder.model,
            "duration_ms": round(ms, 1), "outcome": "ok"})
        return IngestResult(slug, "ingested", chunks=len(chunks),
                            turns=transcript.turn_count,
                            words=transcript.word_count, duration_ms=ms)

    except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
        ms = (time.perf_counter() - t0) * 1000
        log.error("transcript_ingest_failed", extra={
            "slug": slug, "error_class": type(exc).__name__,
            "duration_ms": round(ms, 1), "outcome": "error"})
        return IngestResult(slug, "failed", duration_ms=ms,
                            error=f"{type(exc).__name__}: {exc}")


async def ingest_all(*, force: bool = False, limit: int | None = None,
                     only: str | None = None) -> IngestReport:
    man = load_manifest()
    episodes = man["episodes"]

    if only:
        episodes = [e for e in episodes if e["slug"] == only]
        if not episodes:
            known = ", ".join(e["slug"] for e in man["episodes"])
            raise ValidationFailed(
                f"No episode '{only}' in the pinned manifest. Known: {known}")
    if limit:
        episodes = episodes[:limit]

    embedder = EmbeddingClient()
    report = IngestReport()
    for ep in episodes:
        report.results.append(
            await ingest_one(ep["slug"], ep["sha256"], embedder, force=force))
    return report
