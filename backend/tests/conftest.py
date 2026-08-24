"""Test fixtures.

These are integration tests against a real PostgreSQL — the same engine the app
runs on. An in-memory SQLite substitute would not exercise JSONB, the CASCADE,
or the unique (session_id, seq) index, so it would prove less than it appears to.

Point TEST_DATABASE_URL at a scratch database. It is read from the process
environment first, then from .env, then falls back to the Compose defaults.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def _load_dotenv() -> dict[str, str]:
    """Minimal .env reader.

    conftest must resolve the URL *before* app.config is imported, so this
    cannot go through pydantic-settings.
    """
    out: dict[str, str] = {}
    for candidate in (Path(__file__).parents[1] / ".env",
                      Path(__file__).parents[2] / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        break
    return out


_dotenv = _load_dotenv()

TEST_DB_URL = (
    os.environ.get("TEST_DATABASE_URL")
    or _dotenv.get("TEST_DATABASE_URL")
    or "postgresql://lenny:lenny_dev_password@127.0.0.1:5433/lenny_test"
)

# ---------------------------------------------------------------------------
# Safety guard. The schema fixture calls drop_all(). If TEST_DATABASE_URL ever
# points at a real database -- a stale shell export, a copy-pasted URL, another
# project's Postgres on the default port -- that is data loss with no undo.
# Refuse to run unless the database name is unambiguously a scratch database.
# ---------------------------------------------------------------------------
_db_name = TEST_DB_URL.rsplit("/", 1)[-1].split("?")[0]
if "test" not in _db_name.lower():
    raise RuntimeError(
        f"Refusing to run: TEST_DATABASE_URL points at database {_db_name!r}, "
        "whose name does not contain 'test'. These fixtures DROP ALL TABLES. "
        "Point TEST_DATABASE_URL at a scratch database (e.g. lenny_test)."
    )

os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ.setdefault("LLM_PROVIDER", "ollama")
os.environ.setdefault("LOG_LEVEL", "WARNING")

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import dispose, engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
def _settings_cache_cleared():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
async def _schema(anyio_backend):
    async with engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await dispose()


@pytest.fixture
async def client(_schema):
    app = create_app()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def session_id(client) -> str:
    r = await client.post("/api/sessions", json={"title": "t", "user_metadata": {}})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------------------
# Corpus fixtures (Phase 3).
#
# The ingested corpus is a READ-ONLY shared fixture living in the development
# database, not the scratch test database -- the schema fixtures above call
# drop_all(), and re-ingesting 20 episodes per test session would cost ~53s
# and a live Ollama.
#
# So corpus tests open their own engine against CORPUS_DATABASE_URL (default:
# the dev DATABASE_URL) and only ever read. Tests that need it are skipped,
# loudly, when the corpus has not been ingested.
# ---------------------------------------------------------------------------
CORPUS_DB_URL = (
    os.environ.get("CORPUS_DATABASE_URL")
    or _dotenv.get("DATABASE_URL")
    or "postgresql://lenny:lenny_dev_password@127.0.0.1:5433/lenny"
)
if CORPUS_DB_URL.startswith("postgresql://"):
    CORPUS_DB_URL = CORPUS_DB_URL.replace(
        "postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture(scope="session")
async def corpus_engine(anyio_backend):
    from sqlalchemy.ext.asyncio import create_async_engine
    eng = create_async_engine(CORPUS_DB_URL, pool_pre_ping=True)
    yield eng
    await eng.dispose()


@pytest.fixture
async def corpus_db(corpus_engine):
    """Read-only session over the ingested corpus.

    Rolls back on exit so a stray write in a test can never mutate the corpus.
    """
    from sqlalchemy.ext.asyncio import AsyncSession
    async with AsyncSession(corpus_engine, expire_on_commit=False) as s:
        yield s
        await s.rollback()


@pytest.fixture(scope="session")
async def corpus_ready(corpus_engine) -> int:
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import Chunk
    try:
        async with AsyncSession(corpus_engine) as s:
            n = await s.scalar(select(func.count()).select_from(Chunk))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"corpus database unreachable ({type(exc).__name__}). "
                    f"Start the stack and run: python -m app.ingest")
    if not n:
        pytest.skip("corpus not ingested. Run: python -m app.ingest")
    return n


@pytest.fixture(scope="session")
async def ollama_ready(anyio_backend):
    """Skip embedding-dependent tests when Ollama is not running."""
    from app.embeddings import EmbeddingClient
    try:
        await EmbeddingClient().embed_one("probe")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Ollama unavailable ({type(exc).__name__}). "
                    f"Start it with: ollama serve")
    return True
