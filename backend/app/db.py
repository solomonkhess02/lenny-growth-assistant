"""Async database engine and session factory."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine,
)
from sqlalchemy import text

from .config import get_settings
from .errors import DatabaseUnavailable

log = logging.getLogger("app.db")

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        s = get_settings()
        _engine = create_async_engine(
            s.database_url,
            pool_pre_ping=True,   # a dropped connection surfaces as a retry, not a 500
            pool_size=5,
            max_overflow=5,
            echo=False,
        )
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _factory
    if _factory is None:
        _factory = async_sessionmaker(
            engine(), class_=AsyncSession, expire_on_commit=False)
    return _factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Commits on success, rolls back on any failure."""
    async with session_factory()() as session:
        try:
            yield session
            await session.commit()
        except (SQLAlchemyError, OSError) as exc:
            # OSError matters: when the db container is stopped, Docker drops
            # its DNS entry and the failure surfaces as socket.gaierror during
            # connect -- which is NOT a SQLAlchemyError. Without this the
            # caller got an opaque 500 instead of a retryable 503. Verified by
            # stopping the container, 2026-08-25.
            await session.rollback()
            log.error("database_error", extra={"exc_class": type(exc).__name__})
            raise DatabaseUnavailable() from exc
        except Exception:
            await session.rollback()
            raise


async def ping() -> tuple[bool, str | None]:
    """Health probe. Returns (ok, error_class) — never raises."""
    try:
        async with engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # noqa: BLE001 - health must never propagate
        return False, type(exc).__name__


async def dispose() -> None:
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _factory = None
