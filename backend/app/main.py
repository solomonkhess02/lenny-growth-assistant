"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import dispose, ping
from .errors import register_error_handlers
from .logging_conf import configure_logging, request_id_var
from .providers import close_http_client
from .routers import chat, health, providers, retrieval, sessions

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info("startup", extra={"config": settings.redacted()})

    ok, err = await ping()
    if ok:
        log.info("database_reachable")
    else:
        # Do not crash: a dead DB must be visible via /health, not a boot loop
        # that hides the reason from the operator.
        log.error("database_unreachable_at_startup", extra={"error": err})

    yield

    await close_http_client()
    await dispose()
    log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="The Lenny Growth Assistant",
        version=health.VERSION,
        description="Grounded answers from Lenny's Podcast transcripts.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        response.headers["x-request-id"] = rid
        log.info("request", extra={
            "method": request.method, "path": request.url.path,
            "status": response.status_code, "duration_ms": ms,
        })
        return response

    register_error_handlers(app)

    api = "/api"
    app.include_router(health.router, prefix=api)
    app.include_router(providers.router, prefix=api)
    app.include_router(sessions.router, prefix=api)
    app.include_router(chat.router, prefix=api)
    app.include_router(retrieval.router, prefix=api)

    # Built frontend, when present. Keeps the stack to ONE app container
    # instead of a separate Node server.
    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
        log.info("static_frontend_mounted", extra={"dir": str(static_dir)})

    return app


app = create_app()
