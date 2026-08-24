"""Chunk packing tests.

Offline, against the real transcripts in `spike/evidence/`.

The invariants here are the ones that make a citation trustworthy: a chunk
must know who spoke, when they spoke, and must not silently drop or invent
content. Budget discipline matters too -- the locked Provider UX contract
ties prompt size directly to local latency (~118 tok/s prefill).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.ingest.chunk import (
    CHUNK_TARGET_TOKENS, Chunk, chunk_turns, estimate_tokens,
)
from app.ingest.parse import Turn, merge_backchannel, parse_transcript

EVIDENCE = Path(__file__).parents[2] / "spike" / "evidence"
# carry(<=25% of budget) + one whole turn(<=budget). Never split mid-turn, so
# this ceiling is structural rather than a tuning constant.
MAX_CHUNK_TOKENS = int(CHUNK_TARGET_TOKENS * 1.25)


def _chunks(slug: str) -> list[Chunk]:
    t = parse_transcript(slug, (EVIDENCE / f"{slug}.md").read_bytes())
    return chunk_turns(merge_backchannel(t.turns))


@pytest.fixture(scope="module")
def casey() -> list[Chunk]:
    return _chunks("casey-winters")


@pytest.fixture(scope="module")
def elena() -> list[Chunk]:
    return _chunks("elena-verna")


def test_produces_chunks_from_real_transcript(casey):
    assert len(casey) > 20


def test_chunk_index_is_dense_and_ordered(casey):
    assert [c.chunk_index for c in casey] == list(range(len(casey)))


def test_no_chunk_exceeds_the_structural_ceiling(casey, elena):
    """Regression: the overlap carry once let chunks reach 805 tokens (2x).

    A single large trailing turn was allowed through as overlap, so the next
    chunk began already full.
    """
    for chunks in (casey, elena):
        worst = max(c.token_estimate for c in chunks)
        assert worst <= MAX_CHUNK_TOKENS, f"chunk of {worst} tokens exceeds ceiling"


def test_most_chunks_land_near_budget(casey):
    median = sorted(c.token_estimate for c in casey)[len(casey) // 2]
    assert 250 <= median <= CHUNK_TARGET_TOKENS


def test_every_chunk_has_a_speaker_and_text(casey, elena):
    for chunks in (casey, elena):
        for c in chunks:
            assert c.speaker.strip(), f"chunk {c.chunk_index} has no speaker"
            assert c.text.strip(), f"chunk {c.chunk_index} has no text"


def test_timestamps_are_ordered_and_non_negative(casey):
    for c in casey:
        assert c.start_seconds >= 0
        assert c.end_seconds >= c.start_seconds
    starts = [c.start_seconds for c in casey]
    assert starts == sorted(starts), "chunk starts must be monotonic"


def test_speaker_appears_inline_for_every_voice(elena):
    """skill 02 forbids losing speaker attribution inside a packed chunk."""
    for c in elena:
        assert ":" in c.text
        first = c.text.split("\n")[0]
        assert first.split(":")[0].strip(), "inline attribution missing"


def test_third_speaker_survives_chunking(elena):
    """elena-verna has three speakers; none may be lost in packing."""
    rendered = "\n".join(c.text for c in elena)
    assert "Christina Gilbert" in rendered


def test_dominant_speaker_is_one_of_the_speakers_present(elena):
    for c in elena:
        voices = {line.split(":")[0].strip() for line in c.text.split("\n")}
        assert c.speaker in voices


def test_no_content_is_lost(casey):
    """Every turn's words must appear somewhere in the chunk set."""
    t = parse_transcript("casey-winters", (EVIDENCE / "casey-winters.md").read_bytes())
    merged = merge_backchannel(t.turns)
    haystack = " ".join(c.text for c in casey)
    for turn in merged:
        probe = " ".join(turn.text.split()[:8])
        assert probe in haystack, f"content dropped near {turn.start_seconds}s"


def test_overlap_exists_between_consecutive_chunks(casey):
    """An idea spanning a boundary must be retrievable from either side."""
    overlaps = 0
    for a, b in zip(casey, casey[1:]):
        tail = set(a.text.split()[-40:])
        if len(tail & set(b.text.split()[:60])) > 5:
            overlaps += 1
    assert overlaps > len(casey) * 0.3, "overlap is not actually happening"


def test_chunking_is_deterministic(casey):
    """Same input, byte-identical output -- retrieval determinism starts here."""
    again = _chunks("casey-winters")
    assert [(c.chunk_index, c.speaker, c.text, c.start_seconds, c.end_seconds)
            for c in casey] == \
           [(c.chunk_index, c.speaker, c.text, c.start_seconds, c.end_seconds)
            for c in again]


def test_long_monologue_is_split_without_inventing_timestamps():
    """Fragments keep the ORIGINAL start time.

    We know when the turn began; we do not know when an interior sentence was
    spoken. A plausible-looking interpolated timestamp would send a human
    clicking the citation to the wrong moment.
    """
    long_turn = Turn("Speaker One", 4242, " ".join(["word"] * 2000))
    chunks = chunk_turns([long_turn])
    assert len(chunks) > 1
    assert all(c.start_seconds == 4242 for c in chunks)
    assert all(c.token_estimate <= MAX_CHUNK_TOKENS for c in chunks)


def test_single_short_turn_yields_one_chunk():
    chunks = chunk_turns([Turn("A", 0, "short answer here")])
    assert len(chunks) == 1
    assert chunks[0].speaker == "A"
    assert chunks[0].end_seconds == 0


def test_empty_input_yields_no_chunks():
    assert chunk_turns([]) == []


def test_estimate_tokens_is_proportional():
    assert estimate_tokens("") == 0
    assert estimate_tokens(" ".join(["w"] * 100)) == 133
