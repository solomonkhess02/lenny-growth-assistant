"""Database models.

Phase 2B covers conversation state only: sessions and messages. Transcript,
chunk and artifact tables arrive in Phases 3 and 7.

Session isolation is enforced structurally: every message row carries a
NOT NULL session_id foreign key, and every read path filters on it.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func,
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

    # The provider in force when the session was created. Per-message provider
    # is recorded on the message, since the user may switch mid-session.
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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())

    session: Mapped[Session] = relationship(back_populates="messages")
