"""Health — reports real dependency state, never a hardcoded 200.

Skill 01: failures must be detectable. A health endpoint that always says "ok"
hides exactly what an operator needs to see.
"""
from __future__ import annotations

from fastapi import APIRouter, Response, status

from ..config import get_settings
from ..db import ping
from ..providers import get_provider

router = APIRouter(tags=["health"])

VERSION = "0.2.0-phase2b"


@router.get("/health")
async def health(response: Response) -> dict:
    settings = get_settings()

    db_ok, db_err = await ping()
    database = {"ok": db_ok}
    if db_err:
        database["error"] = db_err
        database["detail"] = (
            "Cannot reach PostgreSQL. Is the `db` service up "
            "(`docker compose ps`) and DATABASE_URL correct?"
        )

    try:
        provider = (await get_provider().check())
    except Exception as exc:  # noqa: BLE001
        provider = {"provider": settings.llm_provider, "ok": False,
                    "detail": f"{type(exc).__name__}"}

    provider_ok = bool(provider.get("reachable")) and bool(
        provider.get("model_available"))

    # The database is required; the provider is required for *generation* only,
    # so a provider outage is "degraded", not "down". Distinguishing them lets
    # an operator tell a broken deployment from an unstarted Ollama.
    if not db_ok:
        state, code = "unhealthy", status.HTTP_503_SERVICE_UNAVAILABLE
    elif not provider_ok:
        state, code = "degraded", status.HTTP_200_OK
    else:
        state, code = "ok", status.HTTP_200_OK
    response.status_code = code

    return {
        "status": state,
        "version": VERSION,
        "database": database,
        "provider": provider,
        "embedding": {
            "model": settings.embedding_model,
            "dim": settings.embedding_dim,
            "note": "configured only; ingestion arrives in Phase 3",
        },
    }


@router.get("/health/live")
async def live() -> dict:
    """Process liveness only — no dependencies touched."""
    return {"status": "alive", "version": VERSION}


@router.get("/config")
async def config() -> dict:
    """Redacted effective configuration. Satisfies §3.2 'selected provider visible'."""
    return get_settings().redacted()
