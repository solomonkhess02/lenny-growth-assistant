"""Health reporting and structured error behaviour."""
from __future__ import annotations

import pytest

from app.errors import AppError, ProviderUnavailable


async def test_liveness_touches_no_dependencies(client):
    r = await client.get("/api/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "alive"


async def test_health_reports_real_dependency_state(client):
    r = await client.get("/api/health")
    body = r.json()
    # DB is up in the test environment, so status is ok or degraded (the
    # latter when Ollama is not running) — never a blind 200 "ok".
    assert body["status"] in {"ok", "degraded"}
    assert body["database"]["ok"] is True
    assert r.status_code == 200

    assert "provider" in body and "model" in body["provider"]
    assert body["embedding"]["model"] == "all-minilm"
    assert body["embedding"]["dim"] == 384


async def test_config_endpoint_never_exposes_the_key(client):
    body = (await client.get("/api/config")).json()
    assert "deepseek_api_key" not in body
    assert body["deepseek_api_key_present"] in (True, False)
    assert body["llm_provider"] == "ollama"
    flat = str(body).lower()
    assert "sk-" not in flat


async def test_unknown_route_is_structured(client):
    r = await client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_security_headers_are_present_on_every_response(client):
    """Phase 7 (docs/artifact-isolation.md, D-4): the app-document half of
    the artifact isolation strategy. `frame-src 'self'` is what lets the
    Artifact Pane's sandboxed srcdoc iframe render at all under this policy.
    """
    r = await client.get("/api/health/live")
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "frame-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    # Deliberately loosened, and only this one directive: a `srcdoc` frame
    # applies the EMBEDDER's CSP in addition to its own <meta> policy, so a
    # strict `style-src 'self'` here silently blocked the essay iframe's own
    # typography <style> block (verified in-browser, not hypothetical).
    # `script-src` stays plain `'self'` -- this is a style-only exception.
    assert "style-src 'self' 'unsafe-inline'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cross-origin-opener-policy"] == "same-origin"


async def test_every_response_carries_a_request_id(client):
    r = await client.get("/api/health/live")
    assert r.headers.get("x-request-id")


async def test_supplied_request_id_is_echoed(client):
    r = await client.get("/api/health/live", headers={"x-request-id": "abc123"})
    assert r.headers["x-request-id"] == "abc123"


def test_error_envelope_shape():
    exc = ProviderUnavailable("Ollama is down.", host="127.0.0.1")
    assert exc.code == "provider_unavailable"
    assert exc.http_status == 503
    assert exc.retryable is True
    assert exc.details == {"host": "127.0.0.1"}


def test_appror_defaults_are_not_retryable():
    assert AppError().retryable is False
    assert AppError().http_status == 500


async def test_unhandled_exception_does_not_leak_internals(_schema, monkeypatch):
    """A crash must return the structured envelope, never a traceback.

    Needs its own client: Starlette's ServerErrorMiddleware returns the 500
    response and then re-raises so the server can log it, and ASGITransport
    defaults to raise_app_exceptions=True -- which would surface the re-raise
    instead of the response a real HTTP client would receive.
    """
    import httpx
    from httpx import ASGITransport

    from app.main import create_app
    from app.routers import health as health_mod

    async def boom():
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(health_mod, "ping", boom)

    transport = ASGITransport(app=create_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/health")

    assert r.status_code == 500
    body = r.json()
    assert body["error"]["code"] == "internal_error"
    assert "secret internal detail" not in str(body)
    assert "Traceback" not in str(body)
    assert "RuntimeError" not in str(body)
