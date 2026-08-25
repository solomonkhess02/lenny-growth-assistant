"""Retrieval tests -- eval cases 5, 7, 13, plus determinism (A8) and A4.

These run against the REAL ingested corpus (20 episodes, 1,395 chunks), not a
synthetic fixture. Skipped with a clear message if the corpus is not ingested
or Ollama is not running.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.embeddings import EmbeddingClient
from app.errors import ProviderMisconfigured
from app.retrieval import Evidence, _citation_url, index_status, retrieve

pytestmark = pytest.mark.usefixtures("corpus_ready", "ollama_ready")

ANSWERABLE = "How does Duolingo use streaks to improve retention?"
DATA_DIR = Path(__file__).parents[2] / "data" / "transcripts"


# --------------------------------------------------------------------------
# Case 5 -- source attribution
# --------------------------------------------------------------------------
async def test_every_contract_field_is_populated(corpus_db):
    """skill 02 names seven fields. None may be blank on any result."""
    evidence = await retrieve(corpus_db, ANSWERABLE, k=3)
    assert evidence
    for e in evidence:
        assert e.source_id, "source_id missing"
        assert e.source_title, "source_title missing"
        assert e.speaker, "speaker missing"
        assert e.source_url, "source_url missing"
        assert e.transcript_id, "transcript_id missing"
        assert e.chunk_id, "chunk_id missing"
        assert e.publish_date is not None, "publication date missing"


async def test_citation_url_deep_links_to_the_timestamp(corpus_db):
    evidence = await retrieve(corpus_db, ANSWERABLE, k=3)
    for e in evidence:
        assert e.citation_url.startswith(e.source_url)
        assert f"t={e.start_seconds}" in e.citation_url


def test_citation_url_construction():
    assert _citation_url("https://youtu.be/x?v=1", 90) == "https://youtu.be/x?v=1&t=90"
    assert _citation_url("https://youtu.be/x", 90) == "https://youtu.be/x?t=90"
    assert _citation_url("", 90) == ""
    # A negative timestamp would produce a broken link.
    assert _citation_url("https://y/z", -5) == "https://y/z?t=0"


async def test_citations_resolve_to_real_source_text(corpus_db):
    """A4: walk the citation back to the file it claims to come from.

    This is the property that separates a citation from a claim. If the stored
    text cannot be found in the on-disk transcript for that slug, the whole
    attribution chain is broken.
    """
    evidence = await retrieve(corpus_db, ANSWERABLE, k=3)
    assert evidence
    for e in evidence:
        path = DATA_DIR / f"{e.source_id}.md"
        assert path.is_file(), f"cited source {e.source_id} has no transcript"
        source = " ".join(path.read_text(encoding="utf-8").split())
        # The chunk renders as "Speaker: text"; check a distinctive run of the
        # spoken words appears verbatim in the source file.
        spoken = e.text.split(":", 1)[1] if ":" in e.text else e.text
        probe = " ".join(spoken.split()[:12])
        assert probe in source, (
            f"cited text not found in {e.source_id}.md: {probe[:60]!r}")


async def test_speaker_is_a_real_speaker_from_that_episode(corpus_db):
    evidence = await retrieve(corpus_db, ANSWERABLE, k=3)
    for e in evidence:
        source = (DATA_DIR / f"{e.source_id}.md").read_text(encoding="utf-8")
        # Speakers are normalised, so accept either the normalised name or the
        # raw alias as written in the file.
        alias = "Lenny" if e.speaker == "Lenny Rachitsky" else e.speaker
        assert e.speaker in source or alias in source, (
            f"speaker {e.speaker!r} does not appear in {e.source_id}.md")


# --------------------------------------------------------------------------
# Case 7 -- empty retrieval
# --------------------------------------------------------------------------
async def test_empty_query_returns_empty(corpus_db):
    assert await retrieve(corpus_db, "") == []
    assert await retrieve(corpus_db, "   ") == []


async def test_impossible_floor_returns_empty_not_error(corpus_db):
    """An unsatisfiable floor must yield [], never an exception."""
    assert await retrieve(corpus_db, ANSWERABLE, k=3, min_similarity=0.999) == []


async def test_empty_result_is_a_clean_list(corpus_db):
    result = await retrieve(corpus_db, "How do I make sourdough starter?", k=3)
    assert result == []
    assert isinstance(result, list)


# --------------------------------------------------------------------------
# Case 13 -- embedding-model mismatch must be refused
# --------------------------------------------------------------------------
async def test_model_mismatch_is_refused(corpus_db):
    """The index is all-minilm/384. Querying it as nomic must raise.

    This guard is the ONLY thing that can catch this: the embedding column is
    dimensionless, so PostgreSQL will compare a 768-wide query vector against
    384-wide rows without complaint.
    """
    class Wrong(EmbeddingClient):
        @property
        def model(self) -> str:
            return "nomic-embed-text"

        @property
        def dim(self) -> int:
            return 768

    with pytest.raises(ProviderMisconfigured) as e:
        await retrieve(corpus_db, ANSWERABLE, k=3, embedder=Wrong())

    msg = str(e.value.message)
    assert "all-minilm" in msg and "nomic-embed-text" in msg
    assert "ingest" in msg, "the error must say how to recover"


async def test_index_status_reports_a_single_coherent_model(corpus_db):
    status = await index_status(corpus_db)
    assert status["populated"]
    assert status["models"] == ["all-minilm"]
    assert status["dims"] == [384]


# --------------------------------------------------------------------------
# A8 -- determinism
# --------------------------------------------------------------------------
async def test_repeated_queries_are_byte_identical(corpus_db):
    """Same question, same evidence, same order -- five times running."""
    def sig(ev: list[Evidence]) -> str:
        return json.dumps([e.to_dict() for e in ev], sort_keys=True)

    first = sig(await retrieve(corpus_db, ANSWERABLE, k=3))
    for i in range(4):
        assert sig(await retrieve(corpus_db, ANSWERABLE, k=3)) == first, \
            f"retrieval changed on run {i + 2}"


async def test_results_are_ordered_by_descending_similarity(corpus_db):
    evidence = await retrieve(corpus_db, ANSWERABLE, k=3)
    sims = [e.similarity for e in evidence]
    assert sims == sorted(sims, reverse=True)


async def test_k_is_an_upper_bound(corpus_db):
    """k caps the result; it is not a quota to be filled.

    Returning fewer, stronger chunks is correct for a grounding system --
    padding to k would mean citing weaker evidence to hit a number.
    """
    for k in (1, 2, 5):
        assert len(await retrieve(corpus_db, ANSWERABLE, k=k,
                                  min_similarity=-1.0)) <= k


async def test_k_is_filled_exactly_when_sources_allow(corpus_db):
    """Uncapped, k is met exactly -- so the bound above is the cap at work."""
    for k in (1, 2, 5):
        assert len(await retrieve(corpus_db, ANSWERABLE, k=k,
                                  min_similarity=-1.0,
                                  max_per_source=None)) == k


async def test_cap_can_return_fewer_than_k_on_a_concentrated_query(corpus_db):
    """Documented consequence of the per-source cap.

    For a question this specific, every near-neighbour comes from the one
    episode that is entirely about it, so a cap of 2 yields 2 results rather
    than reaching further down the ranking for weaker material.
    """
    evidence = await retrieve(corpus_db, ANSWERABLE, k=5,
                              min_similarity=-1.0, max_per_source=2)
    assert len(evidence) == 2
    assert len({e.source_id for e in evidence}) == 1


async def test_chunks_are_never_duplicated(corpus_db):
    evidence = await retrieve(corpus_db, ANSWERABLE, k=5, min_similarity=-1.0)
    ids = [e.chunk_id for e in evidence]
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------
# Per-source cap
# --------------------------------------------------------------------------
async def test_per_source_cap_is_enforced(corpus_db):
    evidence = await retrieve(corpus_db, ANSWERABLE, k=3,
                              min_similarity=-1.0, max_per_source=1)
    assert len({e.source_id for e in evidence}) == len(evidence)


async def test_uncapped_retrieval_may_concentrate_on_one_episode(corpus_db):
    """The cap is a real constraint, not a no-op: without it, this query
    returns three chunks from the same episode."""
    evidence = await retrieve(corpus_db, ANSWERABLE, k=3,
                              min_similarity=-1.0, max_per_source=None)
    assert len({e.source_id for e in evidence}) == 1


# --------------------------------------------------------------------------
# Corpus completeness
# --------------------------------------------------------------------------
async def test_all_twenty_episodes_are_indexed(corpus_db):
    from sqlalchemy import func, select

    from app.models import Transcript
    n = await corpus_db.scalar(select(func.count()).select_from(Transcript))
    assert n == 20, f"expected 20 curated episodes, found {n}"


async def test_no_episode_is_silently_empty(corpus_db):
    """The Phase 1 defect, checked at the storage layer."""
    from sqlalchemy import func, select

    from app.models import Chunk, Transcript
    rows = (await corpus_db.execute(
        select(Transcript.slug, func.count(Chunk.id))
        .outerjoin(Chunk, Chunk.transcript_id == Transcript.id)
        .group_by(Transcript.slug)
    )).all()
    empty = [slug for slug, count in rows if count == 0]
    assert not empty, f"episodes indexed with zero chunks: {empty}"


# --------------------------------------------------------------------------
# Rehydration (Phase 6): reading specific chunks back, not searching for them
# --------------------------------------------------------------------------
class TestEvidenceRehydration:
    """Turning stored citation cards back into the evidence they name.

    This is what lets an essay be written from the evidence a reader actually
    saw. It is a primary-key read, deliberately not a search: a search could
    return different material under the same labels.
    """

    async def test_returns_evidence_in_the_order_requested(self, corpus_db):
        """Order is the caller's, because order IS the citation label."""
        from app.retrieval import evidence_by_chunk_ids, retrieve

        found = await retrieve(corpus_db, "How do streaks improve retention?", 3)
        assert len(found) >= 2, "corpus returned too little to test ordering"

        ids = [e.chunk_id for e in found]
        back = await evidence_by_chunk_ids(corpus_db, ids)
        assert [e.chunk_id for e in back] == ids

        # And reversed, to prove the order is honoured rather than coincidental.
        back_rev = await evidence_by_chunk_ids(corpus_db, list(reversed(ids)))
        assert [e.chunk_id for e in back_rev] == list(reversed(ids))

    async def test_rehydrated_evidence_matches_the_original(self, corpus_db):
        """Same row in, same evidence out -- text, speaker, deep link and all."""
        from app.retrieval import evidence_by_chunk_ids, retrieve

        found = await retrieve(corpus_db, "How do streaks improve retention?", 2)
        back = await evidence_by_chunk_ids(
            corpus_db, [e.chunk_id for e in found],
            similarities={e.chunk_id: e.similarity for e in found})

        for original, restored in zip(found, back):
            assert restored.text == original.text
            assert restored.speaker == original.speaker
            assert restored.source_title == original.source_title
            assert restored.citation_url == original.citation_url
            assert restored.similarity == original.similarity

    async def test_unknown_similarity_is_zero_not_invented(self, corpus_db):
        """A score nothing measured must not appear on a citation card."""
        from app.retrieval import evidence_by_chunk_ids, retrieve

        found = await retrieve(corpus_db, "How do streaks improve retention?", 1)
        back = await evidence_by_chunk_ids(corpus_db, [found[0].chunk_id])
        assert back[0].similarity == 0.0

    async def test_a_missing_chunk_refuses_the_whole_set(self, corpus_db):
        """Partial evidence is not a smaller version of the same answer.

        Re-ingesting replaces chunk ids, so this is the realistic case: better
        to refuse than to write from whatever half still resolves.
        """
        import uuid as _uuid

        import pytest

        from app.errors import EvidenceUnavailable
        from app.retrieval import evidence_by_chunk_ids, retrieve

        found = await retrieve(corpus_db, "How do streaks improve retention?", 1)
        with pytest.raises(EvidenceUnavailable) as exc:
            await evidence_by_chunk_ids(
                corpus_db, [found[0].chunk_id, str(_uuid.uuid4())])
        assert "re-ingested" in str(exc.value), "the error must name the likely cause"

    async def test_malformed_id_is_refused_not_ignored(self, corpus_db):
        import pytest

        from app.errors import EvidenceUnavailable
        from app.retrieval import evidence_by_chunk_ids

        with pytest.raises(EvidenceUnavailable):
            await evidence_by_chunk_ids(corpus_db, ["not-a-uuid"])

    async def test_empty_request_is_an_empty_list(self, corpus_db):
        from app.retrieval import evidence_by_chunk_ids
        assert await evidence_by_chunk_ids(corpus_db, []) == []
