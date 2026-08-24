"""The provider seam.

Skill 04: selection is configuration, never a hardcoded branch. §3.2 requires
switching providers without changing application code — these tests assert
that property directly.
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.errors import ProviderMisconfigured, ProviderUnavailable
from app.providers import (
    DeepSeekProvider, OllamaProvider, available_providers, get_provider,
)


def _settings(**kw) -> Settings:
    base = dict(database_url="postgresql://u:p@127.0.0.1:5432/x")
    base.update(kw)
    return Settings(**base)


def test_both_providers_registered():
    assert available_providers() == ["deepseek", "ollama"]


def test_selection_is_config_only(monkeypatch):
    """The same call site yields a different provider purely from config."""
    from app import providers as mod
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    assert isinstance(mod.get_provider(), DeepSeekProvider)

    get_settings.cache_clear()
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert isinstance(mod.get_provider(), OllamaProvider)

    get_settings.cache_clear()


def test_unknown_provider_is_misconfiguration_not_a_crash():
    with pytest.raises(ProviderMisconfigured) as e:
        get_provider("gpt-fictional")
    assert e.value.code == "provider_misconfigured"
    assert e.value.retryable is False


def test_describe_exposes_selection_for_the_ui():
    p = OllamaProvider(_settings(ollama_model="qwen3:4b-instruct"))
    d = p.describe()
    assert d == {"provider": "ollama", "model": "qwen3:4b-instruct",
                 "base_url": "http://127.0.0.1:11434"}


def test_ollama_default_url_is_not_localhost():
    """Regression guard: `localhost` costs ~2s per connection on Windows."""
    assert "localhost" not in _settings().ollama_base_url
    assert _settings().ollama_base_url == "http://127.0.0.1:11434"


def test_database_url_gets_async_driver():
    s = _settings(database_url="postgresql://u:p@h:5432/db")
    assert s.database_url.startswith("postgresql+asyncpg://")
    # Alembic runs sync and must get the plain URL back.
    assert s.sync_database_url == "postgresql://u:p@h:5432/db"


def test_redacted_config_omits_the_key():
    s = _settings(deepseek_api_key="sk-thisisasecretvalue")
    red = s.redacted()
    assert "sk-thisisasecretvalue" not in str(red)
    assert red["deepseek_api_key_present"] is True


async def test_deepseek_without_key_fails_fast_and_unretryably():
    p = DeepSeekProvider(_settings(deepseek_api_key=""))
    with pytest.raises(ProviderMisconfigured) as e:
        async for _ in p.stream("hi"):
            pass
    assert e.value.retryable is False, "a missing key is not fixed by retrying"


async def test_deepseek_check_reports_missing_key_without_network():
    out = await DeepSeekProvider(_settings(deepseek_api_key="")).check()
    assert out["configured"] is False
    assert "DEEPSEEK_API_KEY" in out["detail"]


async def test_ollama_check_on_dead_host_is_actionable(monkeypatch):
    """Unreachable Ollama must produce guidance, not a bare exception."""
    p = OllamaProvider(_settings(ollama_base_url="http://127.0.0.1:1"))
    out = await p.check()
    assert out["reachable"] is False
    assert out["model_available"] is False
    assert "ollama serve" in out["detail"]
    assert out["latency_ms"] >= 0


async def test_ollama_stream_on_dead_host_raises_retryable():
    p = OllamaProvider(_settings(ollama_base_url="http://127.0.0.1:1"))
    with pytest.raises(ProviderUnavailable) as e:
        async for _ in p.stream("hi"):
            pass
    assert e.value.retryable is True


async def test_provider_endpoints_report_selection(client):
    body = (await client.get("/api/providers")).json()
    assert body["selected"] == "ollama"
    assert set(body["available"]) == {"ollama", "deepseek"}
    assert "No automatic substitution" in body["detail"]


async def test_probe_reports_failure_without_masking_it(client):
    """A dead provider yields ok=false with a code — never a fake success."""
    r = await client.post("/api/providers/probe?name=deepseek")
    body = r.json()
    if not body["ok"]:
        assert body["error"]["code"] in {
            "provider_misconfigured", "provider_unavailable"}
