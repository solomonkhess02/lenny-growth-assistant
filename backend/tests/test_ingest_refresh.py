"""Idempotent refresh -- eval cases 11 and 12.

These write to the SCRATCH test database (conftest points DATABASE_URL there),
so they build and mutate their own small corpus without touching the real
ingested one.

Refresh is where a knowledge base quietly rots: a re-ingest that half-applies
leaves some chunks describing the old text and some the new, and nothing
surfaces the inconsistency. The properties pinned here are that an unchanged
episode is skipped without writing, a changed episode is replaced completely,
and a failed refresh does not destroy what was already good.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import session_factory
from app.embeddings import EmbeddingClient
from app.ingest import fetch as fetch_mod
from app.ingest.pipeline import ingest_one
from app.models import Chunk, Transcript

pytestmark = pytest.mark.usefixtures("ollama_ready")

REAL = Path(__file__).parents[2] / "data" / "transcripts" / "casey-winters.md"
SLUG = "casey-winters"

# Binary so no platform translates it. See
# test_changed_transcript_leaves_no_stale_chunks.
NEW_TURN = (
    chr(10) * 2 + "Casey Winters (55:00):" + chr(10) * 2
    + "A genuinely new closing thought appended by the test." + chr(10)
).encode("utf-8")


@pytest.fixture
def staged(tmp_path, monkeypatch, _schema):
    """A private copy of one real transcript, in a temp corpus directory."""
    if not REAL.is_file():
        pytest.skip("corpus not fetched. Run: python -m app.ingest")
    monkeypatch.setattr(fetch_mod, "DATA_DIR", tmp_path)
    dest = tmp_path / f"{SLUG}.md"
    shutil.copy(REAL, dest)
    return dest


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def counts() -> tuple[int, int]:
    async with session_factory()() as db:
        t = await db.scalar(select(func.count()).select_from(Transcript))
        c = await db.scalar(select(func.count()).select_from(Chunk))
        return t or 0, c or 0


async def stored() -> Transcript | None:
    async with session_factory()() as db:
        return await db.scalar(select(Transcript).where(Transcript.slug == SLUG))


# --------------------------------------------------------------------------
# Case 11 -- unchanged content hash is skipped
# --------------------------------------------------------------------------
async def test_first_ingest_then_unchanged_refresh_is_skipped(staged):
    embedder = EmbeddingClient()

    first = await ingest_one(SLUG, sha(staged), embedder)
    assert first.status == "ingested", first.error
    assert first.chunks > 0
    before = await counts()

    second = await ingest_one(SLUG, sha(staged), embedder)
    assert second.status == "skipped"
    # Reports the real chunk count, not zero -- a skip is not an empty result.
    assert second.chunks == first.chunks
    assert await counts() == before, "a skip must not write anything"


async def test_skip_preserves_row_identity_and_timestamp(staged):
    embedder = EmbeddingClient()
    await ingest_one(SLUG, sha(staged), embedder)
    original = await stored()

    await ingest_one(SLUG, sha(staged), embedder)
    after = await stored()

    assert after.id == original.id, "row was replaced despite no change"
    assert after.ingested_at == original.ingested_at


async def test_force_reingests_despite_unchanged_hash(staged):
    embedder = EmbeddingClient()
    await ingest_one(SLUG, sha(staged), embedder)
    original = await stored()

    forced = await ingest_one(SLUG, sha(staged), embedder, force=True)
    assert forced.status == "ingested"

    after = await stored()
    assert after.id != original.id, "--force did not actually re-ingest"


# --------------------------------------------------------------------------
# Case 12 -- changed transcript is replaced atomically
# --------------------------------------------------------------------------
async def test_changed_transcript_is_fully_replaced(staged):
    embedder = EmbeddingClient()
    await ingest_one(SLUG, sha(staged), embedder)
    old = await stored()
    old_hash, old_id = old.content_hash, old.id

    # Truncate to the first ~40 turns: genuinely different content.
    text = staged.read_text(encoding="utf-8")
    head, body = text.split("---", 2)[1], text.split("---", 2)[2]
    staged.write_text(f"---{head}---{body[:len(body) // 3]}", encoding="utf-8")

    result = await ingest_one(SLUG, sha(staged), embedder)
    assert result.status == "ingested"

    new = await stored()
    assert new.content_hash != old_hash, "content hash did not change"
    assert new.id != old_id, "transcript row was not replaced"
    assert new.word_count < old.word_count

    # No orphans and no mixture of old and new chunks.
    transcripts, chunks = await counts()
    assert transcripts == 1
    async with session_factory()() as db:
        mine = await db.scalar(
            select(func.count()).select_from(Chunk)
            .where(Chunk.transcript_id == new.id))
    assert mine == chunks, "chunks from the previous version survived"
    assert chunks == result.chunks


async def test_changed_transcript_leaves_no_stale_chunks(staged):
    """The CASCADE is real: no chunk may outlive its transcript."""
    embedder = EmbeddingClient()
    await ingest_one(SLUG, sha(staged), embedder)
    old_id = (await stored()).id

    # Append a real new turn, in BINARY, so the change is unambiguous.
    #
    # The previous version did `text.replace("retention", ...)` -- but
    # casey-winters.md contains ZERO occurrences of "retention", so the
    # replace was a no-op and the content hash never changed. It passed on
    # Windows only because read_text/write_text translate line endings
    # (LF -> CRLF), which altered the bytes by accident. On Linux there is no
    # translation: the file was byte-identical, the transcript was correctly
    # SKIPPED, and this test's premise never held.
    #
    # Caught by running the suite inside the container. A binary append
    # removes the platform dependency entirely.
    with staged.open("ab") as fh:
        fh.write(NEW_TURN)

    result = await ingest_one(SLUG, sha(staged), embedder)
    assert result.status == "ingested", "the appended turn did not change the hash"

    async with session_factory()() as db:
        orphans = await db.scalar(
            select(func.count()).select_from(Chunk)
            .where(Chunk.transcript_id == old_id))
    assert orphans == 0


# --------------------------------------------------------------------------
# Embedding-model change forces a re-ingest
# --------------------------------------------------------------------------
async def test_model_change_is_not_treated_as_unchanged(staged):
    """Same text embedded by a different model is a different vector space.

    Treating that as a cache hit would leave the index silently mixed -- the
    one thing the dimensionless vector column cannot protect against.
    """
    await ingest_one(SLUG, sha(staged), EmbeddingClient())

    class Other(EmbeddingClient):
        @property
        def model(self) -> str:
            return "pretend-other-model"

    result = await ingest_one(SLUG, sha(staged), Other())
    assert result.status != "skipped", \
        "an embedding-model change was treated as a cache hit"


# --------------------------------------------------------------------------
# Failure modes -- requirement 13
# --------------------------------------------------------------------------
async def test_zero_turn_transcript_fails_loudly_and_writes_nothing(
        tmp_path, monkeypatch, _schema):
    monkeypatch.setattr(fetch_mod, "DATA_DIR", tmp_path)
    bad = tmp_path / "broken.md"
    bad.write_bytes(b"---\nguest: X\n---\n\nProse with no speaker headers.\n")

    before = await counts()
    result = await ingest_one("broken", sha(bad), EmbeddingClient())

    assert result.status == "failed"
    assert "broken" in result.error
    assert "ZERO" in result.error.upper()
    assert await counts() == before, "a failed ingest wrote rows"


async def test_integrity_mismatch_is_refused(staged):
    """A file whose hash does not match the manifest must not be ingested.

    The wrong hash also makes the fetcher try to re-download; with no network
    route to a bogus slug that fails, and the outcome must still be a clean
    reported failure rather than a partial write.
    """
    before = await counts()
    result = await ingest_one(SLUG, "0" * 64, EmbeddingClient())
    assert result.status == "failed"
    assert await counts() == before


async def test_embedding_failure_leaves_the_previous_version_intact(staged):
    """Ollama going down mid-refresh must not empty the corpus."""
    embedder = EmbeddingClient()
    await ingest_one(SLUG, sha(staged), embedder)
    good = await stored()
    before = await counts()

    class Broken(EmbeddingClient):
        async def embed(self, texts):
            raise RuntimeError("ollama is down")

    staged.write_text(staged.read_text(encoding="utf-8") + "\n\nSpeaker X (99:59):\n\nmore words here to change the hash\n",
                      encoding="utf-8")
    result = await ingest_one(SLUG, sha(staged), Broken())

    assert result.status == "failed"
    assert await counts() == before, "a failed refresh changed the corpus"
    still = await stored()
    assert still.id == good.id, "the previous good version was destroyed"
