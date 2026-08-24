"""Embedding client (Ollama).

OD-2 locked `all-minilm` / 384 dims by measurement: 19-24x faster to ingest
than nomic-embed-text (~60s vs ~23min for the corpus) with no measurable
quality loss on this material, and co-resident with the chat model without
evicting it.

Two transport facts are baked in here because both were measured the hard way:

  - Requests go to 127.0.0.1, never `localhost`. `localhost` resolves ::1
    first, Ollama binds IPv4 only, and every NEW connection stalls ~2s. That
    is configuration (see Settings.ollama_base_url), but it is why...
  - ...we reuse the process-wide pooled client from app.providers rather than
    opening a connection per call. Per-request connections turned a 21ms
    embed into a 2,056ms one.

Vectors from both candidate models arrive L2-normalised (|v| = 1.000), so
cosine distance and inner product agree and no normalisation step is needed.
"""
from __future__ import annotations

import logging
import time

import httpx

from .config import Settings, get_settings
from .errors import ProviderUnavailable, ValidationFailed
from .providers import http_client

log = logging.getLogger("app.embeddings")

# Ollama holds the whole batch in memory and answers in one response. 64 keeps
# a single failure cheap to retry without making the request count silly.
BATCH_SIZE = 64


class EmbeddingClient:
    """Turns text into vectors, or fails loudly. Never returns a wrong width."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def model(self) -> str:
        return self.settings.embedding_model

    @property
    def dim(self) -> int:
        return self.settings.embedding_dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in order. Raises rather than returning partial results.

        A partially-embedded batch is worse than a failed one: it would leave
        the index silently missing chunks that retrieval would then never find.
        """
        if not texts:
            return []

        out: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            out.extend(await self._embed_batch(texts[start:start + BATCH_SIZE]))
        return out

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        t0 = time.perf_counter()
        url = f"{self.settings.ollama_base_url}/api/embed"
        try:
            r = await http_client().post(
                url, json={"model": self.model, "input": batch}, timeout=120.0)
            if r.status_code >= 400:
                body = r.text[:300]
                raise ProviderUnavailable(
                    f"Ollama embedding request returned {r.status_code}. "
                    f"Is '{self.model}' pulled? Run: ollama pull {self.model}",
                    status=r.status_code, body=body)
            payload = r.json()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"Cannot reach Ollama at {self.settings.ollama_base_url} to "
                f"embed ({type(exc).__name__}). Is `ollama serve` running?"
            ) from exc

        vectors = payload.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(batch):
            raise ProviderUnavailable(
                f"Ollama returned {len(vectors) if isinstance(vectors, list) else 'no'} "
                f"embeddings for {len(batch)} inputs. Refusing a partial batch."
            )

        for i, vec in enumerate(vectors):
            # The dimensionless vector column will happily store the wrong
            # width, and mixed widths make every later comparison meaningless.
            # This is the only place that can catch it at write time.
            if len(vec) != self.dim:
                raise ValidationFailed(
                    f"Embedding model '{self.model}' returned {len(vec)} "
                    f"dimensions but EMBEDDING_DIM is {self.dim}. Refusing to "
                    f"store a mismatched vector. Either set EMBEDDING_DIM="
                    f"{len(vec)} and re-ingest the whole corpus, or switch "
                    f"EMBEDDING_MODEL back."
                )
            if i == 0 and not any(vec):
                raise ValidationFailed(
                    f"Embedding model '{self.model}' returned an all-zero "
                    f"vector. Refusing to index unusable embeddings."
                )

        log.info("embed_batch", extra={
            "provider": "ollama", "model": self.model, "count": len(batch),
            "dim": self.dim, "outcome": "ok",
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1)})
        return [[float(x) for x in v] for v in vectors]
