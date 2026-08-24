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
