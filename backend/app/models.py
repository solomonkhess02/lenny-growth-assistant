"""Database models.

Phase 2B covered conversation state: sessions and messages. Phase 3 adds the
knowledge base: transcripts and chunks. Artifact tables arrive in Phase 7.

Session isolation is enforced structurally: every message row carries a
NOT NULL session_id foreign key, and every read path filters on it.

Citation integrity is enforced the same way. Chunk text, the full skill-02
metadata contract, and the embedding live in ONE row, so a citation is a
foreign key rather than a cross-system join -- the decisive argument for
pgvector over FAISS/Chroma in OD-1.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # §3.1 requires user metadata be persisted. No auth in this build, so this
    # is an opaque bag (client label, user agent) rather than an identity.
    user_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}")

    # Chosen when the session is created, and IMMUTABLE thereafter: no route
    # mutates these, and MessageCreate carries no provider field. Switching
    # provider means creating a new session. That is what lets these two
    # columns be read as a true record of what produced every turn here,
    # rather than a snapshot of whatever configuration held at creation time.
    #
    # The same pair is copied onto each assistant message as well, so a turn
    # remains self-describing when read on its own.
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now())

    messages: Mapped[list["Message"]] = relationship(
        back_populates="session", cascade="all, delete-orphan",
        order_by="Message.seq",
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant','system')", name="ck_messages_role"),
        Index("ix_messages_session_seq", "session_id", "seq", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ON DELETE CASCADE so deleting a session cannot orphan messages, and
    # NOT NULL so a message can never exist outside a session.
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # Monotonic within a session. Ordering by timestamp is unreliable when two
    # rows land in the same millisecond.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Provenance. Locked Provider UX contract requirement 7: every generated
    # artifact identifies the provider/model that produced it.
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # The evidence this turn was built from, exactly as it was streamed to the
    # client. Every field originates in a stored chunk row (agent.source_
    # summaries), never in model output, so replayed citations are as
    # trustworthy as live ones -- and an answer cannot lose its attribution
    # just because the reader refreshed the page.
    sources: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]")

    # The verification verdict. NULLABLE, and the distinction matters: NULL
    # means no verdict was recorded (a user turn, or a row written before
    # Phase 5), which is not the same claim as a recorded PASS. An unverified
    # answer must never be able to read as a verified one.
    grounding: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())

    session: Mapped[Session] = relationship(back_populates="messages")


class Transcript(Base):
    """One podcast episode.

    `slug` is the identifier that ties everything together: it is the corpus
    manifest key, the on-disk filename, this row, and the `source_id` in every
    citation. One name end to end means a citation can always be walked back
    to the exact source file.
    """
    __tablename__ = "transcripts"
    __table_args__ = (
        # The Phase 1 silent-drop defect made unrepresentable. casey-winters
        # once parsed to zero turns and was excluded with no error; a corpus
        # that is quietly incomplete produces confident wrong answers.
        CheckConstraint("turn_count > 0", name="ck_transcripts_turn_count"),
        CheckConstraint("embedding_dim > 0", name="ck_transcripts_embedding_dim"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # skill 02: source_id
    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)

    guest: Mapped[str] = mapped_column(String(200), nullable=False)
    # skill 02: source_title
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # skill 02: source_url. Chunk-level deep links are derived from this plus
    # the chunk's start_seconds; the episode URL itself is stored once here.
    youtube_url: Mapped[str] = mapped_column(Text, nullable=False)
    video_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # skill 02: publication date
    publish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    channel: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    keywords: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]")

    # sha256 of the raw file. Idempotent refresh compares this; an unchanged
    # hash AND an unchanged embedding model means the episode is skipped.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # Persisted so a model change is DETECTABLE. The dimensionless vector
    # column will happily store 384- and 768-wide vectors side by side, so the
    # database cannot catch a mixed index -- this is the guard that can.
    embedding_model: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)

    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan",
        order_by="Chunk.chunk_index",
    )

    # `duration` / `duration_seconds` from the frontmatter are deliberately
    # NOT stored: they describe the linked YouTube clip, not the transcript.
    # casey-winters claims 99s against a transcript running to 3,290s.


class Chunk(Base):
    """A retrievable, individually citable span of one episode."""
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("transcript_id", "chunk_index",
                         name="uq_chunks_transcript_index"),
        CheckConstraint("end_seconds >= start_seconds",
                        name="ck_chunks_time_order"),
        CheckConstraint("start_seconds >= 0", name="ck_chunks_start_nonneg"),
        Index("ix_chunks_transcript_id", "transcript_id"),
    )

    # skill 02: chunk_id
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # skill 02: transcript_id. CASCADE so re-ingesting an episode cannot
    # strand chunks pointing at a transcript that no longer exists.
    transcript_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transcripts.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Position within the episode. Human-readable half of the chunk identity.
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # skill 02: speaker. Per CHUNK, not per transcript -- 4 of the 20 curated
    # episodes have three or more speakers.
    speaker: Mapped[str] = mapped_column(String(200), nullable=False)

    text: Mapped[str] = mapped_column(Text, nullable=False)

    # Timestamp-level attribution. start_seconds is what makes a citation
    # independently verifiable by a human: youtube_url + "&t={start_seconds}".
    start_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    end_seconds: Mapped[int] = mapped_column(Integer, nullable=False)

    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False)

    # Dimensionless on purpose (OD-1 locks exact search with NO ANN index, and
    # only an index requires a fixed width). Switching embedding model is then
    # an env change plus a re-ingest, not a schema migration.
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)

    embedding_model: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())

    transcript: Mapped[Transcript] = relationship(back_populates="chunks")
