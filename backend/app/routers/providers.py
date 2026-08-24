"""Provider inspection and connectivity probe.

`/providers/probe` performs a real round trip to the selected provider. It is
how Phase 2B verifies container -> host Ollama reachability, and how an
operator distinguishes "app broken" from "Ollama not started".
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Query

from ..config import get_settings
from ..errors import AppError
from ..providers import available_providers, get_provider

log = logging.getLogger("app.providers.api")
router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("")
async def list_providers() -> dict:
    settings = get_settings()
    return {
        "selected": settings.llm_provider,
        "available": available_providers(),
        "detail": "Selection is configuration only (LLM_PROVIDER). "
                  "No automatic substitution exists anywhere in the system.",
    }


@router.get("/check")
async def check(name: str | None = Query(default=None)) -> dict:
    return await get_provider(name).check()


@router.post("/probe")
async def probe(
    name: str | None = Query(default=None),
    prompt: str = Query(default="Reply with the single word: OK"),
    max_chars: int = Query(default=200, ge=1, le=4000),
) -> dict:
    """Real generation round trip. Proves end-to-end reachability."""
    provider = get_provider(name)
    t0 = time.perf_counter()
    out: list[str] = []
    try:
        async for delta in provider.stream(prompt):
            out.append(delta)
            if sum(len(x) for x in out) >= max_chars:
                break
        text = "".join(out)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        log.info("provider_probe", extra={
            "provider": provider.name, "model": provider.model,
            "duration_ms": ms, "outcome": "ok"})
        return {"ok": True, **provider.describe(),
                "duration_ms": ms, "chars": len(text),
                "text": text[:max_chars]}
    except AppError as exc:
        ms = round((time.perf_counter() - t0) * 1000, 1)
        log.warning("provider_probe_failed", extra={
            "provider": provider.name, "model": provider.model,
            "duration_ms": ms, "error_code": exc.code, "outcome": "error"})
        # Deliberately 200 with ok=false: the probe *succeeded* at telling you
        # the provider is unreachable. That is diagnostic information.
        return {"ok": False, **provider.describe(), "duration_ms": ms,
                "error": {"code": exc.code, "message": exc.message,
                          "retryable": exc.retryable}}
