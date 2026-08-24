"""The provider seam.

Skill 04: business logic depends on this interface, never on a concrete
provider. Selection is configuration only. Phase 1 proved the seam by serving
both Ollama and DeepSeek from one function with zero application-code change.

Phase 2B implements: the interface, config-driven selection, health checks,
and streaming transport. The agent layer that *uses* stream() arrives in
Phase 4 — the chat endpoint here streams a deterministic placeholder so the
skeleton needs no model to run and tests stay hermetic.

One pooled AsyncClient is shared process-wide. Per-request connections cost
~2s against `localhost` on Windows (IPv6 ::1 first-resolution) and are pure
overhead everywhere else.
"""
from __future__ import annotations

import abc
import json
import logging
import time
from collections.abc import AsyncIterator

import httpx

from .config import Settings, get_settings
from .errors import ProviderMisconfigured, ProviderUnavailable

log = logging.getLogger("app.providers")

_client: httpx.AsyncClient | None = None


def http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


class ModelProvider(abc.ABC):
    name: str

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    @abc.abstractmethod
    def model(self) -> str: ...

    @property
    @abc.abstractmethod
    def base_url(self) -> str: ...

    @abc.abstractmethod
    async def check(self) -> dict:
        """Never raises. Returns a health dict describing real reachability."""

    @abc.abstractmethod
    def stream(self, prompt: str) -> AsyncIterator[str]:
        """Yield text deltas. Raises ProviderUnavailable / ProviderMisconfigured."""

    def describe(self) -> dict:
        return {"provider": self.name, "model": self.model, "base_url": self.base_url}


class OllamaProvider(ModelProvider):
    name = "ollama"

    @property
    def model(self) -> str:
        return self.settings.ollama_model

    @property
    def base_url(self) -> str:
        return self.settings.ollama_base_url

    async def check(self) -> dict:
        """Reachable AND has the configured model. Both are real failure modes."""
        out: dict = {"provider": self.name, "model": self.model,
                     "base_url": self.base_url, "reachable": False,
                     "model_available": False, "configured": True}
        t0 = time.perf_counter()
        try:
            r = await http_client().get(f"{self.base_url}/api/tags", timeout=5.0)
            r.raise_for_status()
            out["reachable"] = True
            names = {m.get("name", "") for m in r.json().get("models", [])}
            out["model_available"] = any(
                n == self.model or n.split(":")[0] == self.model.split(":")[0]
                for n in names
            )
            if not out["model_available"]:
                out["detail"] = (
                    f"Ollama is up but '{self.model}' is not pulled. "
                    f"Run: ollama pull {self.model}"
                )
        except Exception as exc:  # noqa: BLE001 — health must not propagate
            out["detail"] = (
                f"Cannot reach Ollama at {self.base_url} ({type(exc).__name__}). "
                "Is `ollama serve` running on the host? From a container use "
                "host.docker.internal, not 127.0.0.1. On Docker Desktop that "
                "works even with Ollama bound to 127.0.0.1; on Linux with "
                "host-gateway, set OLLAMA_HOST=0.0.0.0 on the host."
            )
        out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "options": {"num_ctx": self.settings.ollama_context_length},
        }
        t0 = time.perf_counter()
        try:
            async with http_client().stream(
                "POST", f"{self.base_url}/api/chat", json=payload
            ) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode(errors="replace")[:300]
                    raise ProviderUnavailable(
                        f"Ollama returned {r.status_code}.", status=r.status_code,
                        body=body)
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("ollama_malformed_line", extra={"line": line[:200]})
                        continue
                    if obj.get("error"):
                        raise ProviderUnavailable(str(obj["error"]))
                    delta = (obj.get("message") or {}).get("content", "")
                    if delta:
                        yield delta
                    if obj.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"Ollama transport failure: {type(exc).__name__}") from exc
        finally:
            log.info("provider_stream_finished", extra={
                "provider": self.name, "model": self.model,
                "duration_ms": round((time.perf_counter() - t0) * 1000, 1)})


class DeepSeekProvider(ModelProvider):
    name = "deepseek"

    @property
    def model(self) -> str:
        return self.settings.deepseek_model

    @property
    def base_url(self) -> str:
        return self.settings.deepseek_base_url

    async def check(self) -> dict:
        configured = bool(self.settings.deepseek_api_key)
        out: dict = {"provider": self.name, "model": self.model,
                     "base_url": self.base_url, "configured": configured,
                     "reachable": False, "model_available": configured}
        if not configured:
            out["detail"] = "DEEPSEEK_API_KEY is not set."
            return out
        t0 = time.perf_counter()
        try:
            # Cheapest liveness probe that does not spend tokens: a HEAD-ish
            # request. Any HTTP answer proves the endpoint is reachable.
            r = await http_client().get(self.base_url, timeout=5.0)
            out["reachable"] = r.status_code < 500
        except Exception as exc:  # noqa: BLE001
            out["detail"] = f"Cannot reach {self.base_url} ({type(exc).__name__})."
        out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        if not self.settings.deepseek_api_key:
            raise ProviderMisconfigured(
                "DEEPSEEK_API_KEY is not set. Set it in .env, or switch "
                "LLM_PROVIDER=ollama.")
        payload: dict = {
            "model": self.model,
            "max_tokens": self.settings.deepseek_max_tokens,
            "stream": True,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.settings.deepseek_disable_thinking:
            # Phase 1: v4-pro thinks by default and a 29,995-char thinking
            # block exhausted an 8192 budget, returning zero usable text.
            payload["thinking"] = {"type": "disabled"}

        headers = {
            "x-api-key": self.settings.deepseek_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        t0 = time.perf_counter()
        try:
            async with http_client().stream(
                "POST", f"{self.base_url}/v1/messages",
                json=payload, headers=headers,
            ) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode(errors="replace")[:300]
                    if r.status_code in (401, 403):
                        raise ProviderMisconfigured(
                            "DeepSeek rejected the API key.", status=r.status_code)
                    raise ProviderUnavailable(
                        f"DeepSeek returned {r.status_code}.",
                        status=r.status_code, body=body)
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        log.warning("deepseek_malformed_sse", extra={"line": data[:200]})
                        continue
                    if obj.get("type") == "content_block_delta":
                        delta = (obj.get("delta") or {}).get("text", "")
                        if delta:
                            yield delta
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"DeepSeek transport failure: {type(exc).__name__}") from exc
        finally:
            log.info("provider_stream_finished", extra={
                "provider": self.name, "model": self.model,
                "duration_ms": round((time.perf_counter() - t0) * 1000, 1)})


_REGISTRY: dict[str, type[ModelProvider]] = {
    "ollama": OllamaProvider,
    "deepseek": DeepSeekProvider,
}


def get_provider(name: str | None = None) -> ModelProvider:
    """Selection is configuration. No caller branches on provider identity."""
    settings = get_settings()
    key = (name or settings.llm_provider).lower()
    cls = _REGISTRY.get(key)
    if cls is None:
        raise ProviderMisconfigured(
            f"Unknown provider '{key}'. Known: {sorted(_REGISTRY)}.")
    return cls(settings)


def available_providers() -> list[str]:
    return sorted(_REGISTRY)
