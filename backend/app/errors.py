"""Structured errors — one envelope shape for every failure.

Skill 01: failures must be detectable, logged, surfaced, and recoverable where
possible. Never hidden. A caller can branch on `error.code` without parsing
prose, and a stack trace never reaches the client.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Starlette renamed this constant; support both so the app works across versions.
# Note: a getattr() default is evaluated eagerly, so the obvious one-liner
# touches the deprecated name every time and emits the warning it avoids.
_HTTP_422 = 422

from .logging_conf import request_id_var

log = logging.getLogger("app.errors")


class AppError(Exception):
    """Base for every failure we raise deliberately."""

    code = "internal_error"
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    message = "An unexpected error occurred."
    retryable = False

    def __init__(self, message: str | None = None, **details: Any) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message
        self.details = details


class NotFoundError(AppError):
    code = "not_found"
    http_status = status.HTTP_404_NOT_FOUND
    message = "The requested resource does not exist."


class ValidationFailed(AppError):
    code = "validation_failed"
    http_status = _HTTP_422
    message = "The request body failed validation."


class DatabaseUnavailable(AppError):
    code = "database_unavailable"
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    message = "The database is not reachable."
    retryable = True


class ProviderUnavailable(AppError):
    """Ollama down, DeepSeek unreachable, model missing. Surfaced, never masked."""

    code = "provider_unavailable"
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    message = "The selected model provider is not reachable."
    retryable = True


class ProviderMisconfigured(AppError):
    """Missing API key and similar — retrying will not help."""

    code = "provider_misconfigured"
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    message = "The selected model provider is not configured correctly."


def envelope(code: str, message: str, *, retryable: bool = False,
             details: dict | None = None) -> dict:
    body: dict[str, Any] = {
        "error": {"code": code, "message": message, "retryable": retryable}
    }
    if details:
        body["error"]["details"] = details
    rid = request_id_var.get()
    if rid:
        body["error"]["request_id"] = rid
    return body


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        log.warning("handled_app_error", extra={
            "error_code": exc.code, "error_message": exc.message,
            "details": exc.details,
        })
        return JSONResponse(
            status_code=exc.http_status,
            content=envelope(exc.code, exc.message,
                             retryable=exc.retryable, details=exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        log.info("request_validation_failed", extra={"errors": exc.errors()})
        return JSONResponse(
            status_code=_HTTP_422,
            content=envelope(
                "validation_failed",
                "The request body failed validation.",
                details={"fields": [
                    {"loc": list(e.get("loc", [])), "msg": e.get("msg", "")}
                    for e in exc.errors()
                ]},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=envelope(code, str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # Log the full trace server-side; return none of it to the client.
        log.exception("unhandled_exception", extra={"exc_class": type(exc).__name__})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=envelope("internal_error",
                             "An unexpected error occurred."),
        )
