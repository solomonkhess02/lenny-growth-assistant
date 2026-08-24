"""Structured JSON logging — one JSON object per line, no exceptions.

Hand-rolled rather than pulling a dependency: the schema is ours, and skill 01
prefers few dependencies. A request_id travels via contextvar so every line
emitted while handling a request is correlatable without threading an argument
through every call.
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes LogRecord always carries; anything else the caller attached via
# `extra=` is application context worth emitting.
_STD = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
}

_SECRET_HINTS = ("key", "token", "secret", "password", "authorization")


def _scrub(k: str, v: object) -> object:
    """Defence in depth: never let a credential reach a log line."""
    if any(h in k.lower() for h in _SECRET_HINTS):
        return "***REDACTED***"
    return v


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        for k, v in record.__dict__.items():
            if k not in _STD and not k.startswith("_"):
                payload[k] = _scrub(k, v)
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn ships its own handlers; force everything through ours so the
    # stream stays parseable line-by-line.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
