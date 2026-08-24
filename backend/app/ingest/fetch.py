"""Corpus acquisition against a pinned manifest.

The upstream archive (ChatPRD/lennys-podcast-transcripts) carries NO licence
file, and this repository is public. So transcripts are not vendored here:
the manifest pins identity (corpus commit + slug) and integrity (sha256), and
the files are fetched into a git-ignored `data/` directory at ingest time.

That choice also buys something real. A pinned hash means the corpus an
evaluator ingests is byte-identical to the one these results were measured
against -- "works on my machine" becomes a checkable claim.
"""
from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path

import httpx

from ..errors import ValidationFailed
from ..providers import http_client

log = logging.getLogger("app.ingest.fetch")

MANIFEST_PATH = Path(__file__).parents[1] / "corpus" / "manifest.json"
# backend/app/ingest/fetch.py -> repo root -> data/transcripts
DATA_DIR = Path(__file__).parents[3] / "data" / "transcripts"


@lru_cache
def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def local_path(slug: str) -> Path:
    return DATA_DIR / f"{slug}.md"


def _verify(slug: str, raw: bytes, expected: str) -> None:
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise ValidationFailed(
            f"Integrity check FAILED for '{slug}': expected sha256 {expected}, "
            f"got {actual}. The upstream file changed or the download is "
            f"corrupt. Refusing to ingest unverified content."
        )


async def ensure_local(slug: str, expected_sha256: str, *,
                       force: bool = False) -> bytes:
    """Return the transcript bytes, downloading only if needed.

    A cached file that fails its hash is re-downloaded once rather than
    trusted -- a truncated earlier run should self-heal, not poison the index.
    """
    path = local_path(slug)
    if path.is_file() and not force:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() == expected_sha256:
            return raw
        log.warning("cached_transcript_hash_mismatch",
                    extra={"slug": slug, "action": "redownloading"})

    man = load_manifest()
    url = man["raw_url_template"].format(commit=man["corpus_commit"], slug=slug)
    try:
        r = await http_client().get(url, timeout=60.0)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValidationFailed(
            f"Could not download transcript '{slug}' from the pinned corpus "
            f"({type(exc).__name__}). Ingestion needs network access once; "
            f"see the README prerequisites."
        ) from exc

    raw = r.content
    _verify(slug, raw, expected_sha256)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    log.info("transcript_downloaded", extra={"slug": slug, "bytes": len(raw)})
    return raw
