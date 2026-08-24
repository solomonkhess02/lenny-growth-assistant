"""Request/response contracts."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    user_metadata: dict = Field(default_factory=dict)


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
