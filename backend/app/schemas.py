"""Request/response contracts."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    user_metadata: dict = Field(default_factory=dict)

    # Provider selection happens HERE and only here. Once a session exists its
    # provider is immutable -- there is no PATCH, and MessageCreate deliberately
    # carries no provider field. Changing provider means starting a new session,
    # which is what keeps sessions.provider/model an honest record of what
    # actually produced every turn in the session rather than a snapshot of
    # whatever configuration held at the time.
    #
    # Plain `str`, not a Literal: the registry in app.providers is the single
    # source of truth for which providers exist, and the router validates
    # against it. A Literal here would be a second list to keep in sync.
    provider: str | None = Field(
        default=None,
        description="Provider for this session. Defaults to LLM_PROVIDER. "
                    "Immutable once the session is created.",
    )


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    seq: int
    role: str
    content: str
    provider: str | None = None
    model: str | None = None
    latency_ms: int | None = None

    # Replay fidelity: a reopened session must show the same citations and the
    # same verdict it showed live. `grounding is None` means no verdict was
    # recorded -- deliberately distinct from a recorded PASS.
    sources: list[dict] = Field(default_factory=list)
    grounding: dict | None = None

    created_at: datetime


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    user_metadata: dict
    provider: str
    model: str
    created_at: datetime
    updated_at: datetime


class SessionDetail(SessionOut):
    messages: list[MessageOut] = Field(default_factory=list)


class MessageCreate(BaseModel):
    """One user turn.

    Carries no provider field, and must not grow one. The provider is a
    property of the session (see SessionCreate); a per-message override would
    let a single conversation span providers and make the provenance stamped on
    sessions.provider a lie.
    """

    content: str = Field(min_length=1, max_length=32_000)


class ProviderInfo(BaseModel):
    provider: str
    model: str
    base_url: str
    configured: bool
    detail: str | None = None


class HealthOut(BaseModel):
    status: str
    database: dict
    provider: dict
    embedding: dict
    version: str
