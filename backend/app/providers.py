"""The provider seam.

Skill 04: business logic depends on this interface, never on a concrete
provider. Selection is configuration only. Phase 1 proved the seam by serving
both Ollama and DeepSeek from one function with zero application-code change.

Phase 4 adoption: generation now runs through the **Pi Coding Agent**, the
§3.1 agent framework. The seam itself is unchanged — `get_provider()` still
selects by configuration and no business logic branches on provider identity.
Pi is the execution engine *behind* both providers, which is why adopting it
required no change to app/agent.py at all.

Each provider therefore owns two things: how to health-check its own endpoint
(direct HTTP, so a health probe never depends on the agent framework), and
which Pi provider/model name it maps to.

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
from .pi_runtime import PiRuntime

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

    @property
    @abc.abstractmethod
    def pi_provider(self) -> str:
        """Provider name as Pi knows it.

        Must match Pi's own naming: `pi-ai/dist/env-api-keys.js` maps provider
        name -> credential env var by exact string, so 'deepseek' resolves
        DEEPSEEK_API_KEY. A mismatch here silently breaks authentication.
        """

    @abc.abstractmethod
    async def check(self) -> dict:
        """Never raises. Returns a health dict describing real reachability."""

    def describe(self) -> dict:
        return {"provider": self.name, "model": self.model,
                "base_url": self.base_url, "agent_framework": "pi",
                "pi_provider": self.pi_provider}

    def stream(self, prompt: str) -> AsyncIterator[str]:
        """Generation runs through Pi for every provider.

        Concrete providers do not override this. Keeping one implementation is
        what makes "switch provider by configuration" true by construction
        rather than by convention.
        """
        return PiRuntime(self.settings).stream(
            pi_provider=self.pi_provider, model=self.model, prompt=prompt)


class OllamaProvider(ModelProvider):
    name = "ollama"

    @property
    def pi_provider(self) -> str:
        # Configured in ~/.pi/agent/models.json against 127.0.0.1 (never
        # `localhost`: it resolves ::1 first and Ollama binds IPv4 only,
        # costing ~2s on every new connection).
        return "ollama"

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

class DeepSeekProvider(ModelProvider):
    name = "deepseek"

    @property
    def pi_provider(self) -> str:
        # MUST be exactly "deepseek": Pi maps provider name -> DEEPSEEK_API_KEY
        # by exact string. It is also a BUILT-IN Pi provider, so no
        # models.json entry may be added for it -- a custom entry with this
        # name shadows the built-in and breaks credential resolution.
        return "deepseek"

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
