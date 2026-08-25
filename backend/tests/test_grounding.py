"""Quote-verification tests -- eval case 14.

Offline, and run against REAL Phase 1 model output in `spike/results/`. That
matters: a fabrication detector validated only on synthetic examples proves
nothing about the fabrications models actually produce.

The headline regression is `test_catches_the_real_phase1_fabrications`, which
pins a defect in the *previous* version of this harness, not in a model.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.grounding import (
    MIN_QUOTE_WORDS, extract_quotes, normalize, verify_answer,
)

SPIKE = Path(__file__).parents[2] / "spike"


@pytest.fixture(scope="module")
def evidence() -> list[dict]:
    return json.loads((SPIKE / "results" / "evidence_set.json")
                      .read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bench() -> dict:
    return json.loads((SPIKE / "results" / "bench.json")
                      .read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# The real fabrications
# --------------------------------------------------------------------------
def test_catches_the_real_llama_fabrication(bench, evidence):
    """llama3.2:3b invented a quote in Test A. This is why it was rejected."""
    report = verify_answer(bench["llama3.2:3b"]["A"]["text"], evidence)
    assert not report.grounded
    assert report.verdict == "FAIL"
    assert len(report.fabricated_quotes) == 1
    assert "distribution platforms" in report.fabricated_quotes[0]


def test_catches_the_real_phase1_fabrications(bench, evidence):
    """qwen3:4b-instruct fabricated 4 quotes in the Ship 30 essay (Test C).

    Phase 1 recorded qwen3 as '0 fabricated quotes'. That was WRONG, and the
    error was in the old harness, not the model: `spike/verify_quotes.py`
    matched only straight ASCII quotes, while qwen3 emitted curly ones. Twenty
    quotations were never examined.

    Verified against the full transcripts, not just the 4-chunk evidence set:
    none of these spans appear anywhere in the source material, so they are
    invented rather than quoted from outside the evidence window.
    """
    report = verify_answer(bench["qwen3:4b-instruct"]["C"]["text"], evidence)
    assert not report.grounded
    # 28/6 rather than the 20/4 first recorded: dropping the 25-character
    # floor for a 2-word rule exposed two further fabrications in the same
    # essay. See test_short_attributed_quote_is_checked.
    assert report.quotes_found == 28, "curly-quoted spans must be examined"
    assert len(report.fabricated_quotes) == 6


def test_curly_quotes_are_examined():
    """The exact blind spot that produced the false 'PASS' above."""
    evidence = [{"text": "Retention is the single most important metric.",
                 "source_title": "T", "speaker": "S"}]
    curly = 'He said “growth is mostly a distribution problem” in the episode.'
    report = verify_answer(curly, evidence)
    assert report.quotes_found == 1, "curly quotes were skipped"
    assert len(report.fabricated_quotes) == 1


# --------------------------------------------------------------------------
# Honest answers must PASS -- a detector that flags everything is useless
# --------------------------------------------------------------------------
def test_grounded_answers_pass(bench, evidence):
    """qwen3 Tests A and B were genuinely clean."""
    for tag in ("A", "B"):
        report = verify_answer(bench["qwen3:4b-instruct"][tag]["text"], evidence)
        assert report.grounded, f"test {tag} wrongly flagged: {report.to_dict()}"


def test_exact_quote_from_evidence_passes(evidence):
    source = evidence[0]["text"]
    quote = " ".join(source.split()[:14])
    report = verify_answer(f'The guest explains: "{quote}" [E1]', evidence)
    assert report.grounded, report.to_dict()


def test_restyled_punctuation_is_not_fabrication(evidence):
    """Curly apostrophes and doubled spaces are formatting, not invention."""
    source = " ".join(evidence[0]["text"].split()[:16])
    restyled = source.replace("'", "’").replace(" ", "  ")
    report = verify_answer(f'"{restyled}"', evidence)
    assert report.grounded, report.to_dict()


def test_quoting_the_episode_title_is_not_fabrication(evidence):
    """Titles are part of the evidence block the model is shown."""
    title = evidence[0]["source_title"]
    if len(title) >= 25:
        assert verify_answer(f'From "{title}".', evidence).grounded


# --------------------------------------------------------------------------
# Citation tags
# --------------------------------------------------------------------------
def test_invalid_citation_tag_is_caught(evidence):
    report = verify_answer("Growth compounds [E1] and also [E99].", evidence)
    assert not report.grounded
    assert report.invalid_tags == ["E99"]


def test_valid_tags_within_range_pass(evidence):
    tags = " ".join(f"[E{i}]" for i in range(1, len(evidence) + 1))
    report = verify_answer(f"Several points {tags}.", evidence)
    assert report.grounded
    assert report.invalid_tags == []


def test_tag_beyond_evidence_count_is_invalid():
    one = [{"text": "x" * 60, "source_title": "T", "speaker": "S"}]
    assert verify_answer("Claim [E2].", one).invalid_tags == ["E2"]


# --------------------------------------------------------------------------
# Boundaries
# --------------------------------------------------------------------------
def test_no_evidence_makes_every_tag_invalid():
    report = verify_answer("Confident claim [E1].", [])
    assert not report.grounded
    assert report.invalid_tags == ["E1"]


def test_empty_answer_is_vacuously_grounded(evidence):
    report = verify_answer("", evidence)
    assert report.grounded
    assert report.quotes_found == 0


def test_answer_without_quotes_or_tags_is_grounded(evidence):
    assert verify_answer("Retention matters a great deal.", evidence).grounded


def test_single_word_emphasis_is_ignored(evidence):
    """Scare-quotes around one word are emphasis, not attribution."""
    assert verify_answer('the "retention" number', evidence).quotes_found == 0
    assert MIN_QUOTE_WORDS == 2


def test_short_attributed_quote_is_checked(evidence):
    """Regression for a REAL fabrication the old threshold hid.

    DeepSeek attributed "golden goose" (12 characters) to an episode that
    never says it, and grounding reported PASS because the span fell under a
    25-character floor. Punchy short phrases are exactly what a model invents
    and a reader repeats, so length was the wrong axis.
    """
    report = verify_answer(
        'He called it a "golden goose" for retention.', evidence)
    assert report.quotes_found == 1, "short attributed quote was not examined"
    assert report.fabricated_quotes == ["golden goose"]
    assert not report.grounded


def test_extract_quotes_handles_both_styles():
    """Straight and curly spans are both examined."""
    text = ('He said "growth is a distribution problem" '
            'and she said “retention beats acquisition”.')
    quotes = extract_quotes(text)
    assert len(quotes) == 2
    assert "growth is a distribution problem" in quotes
    assert "retention beats acquisition" in quotes


def test_extraction_is_by_word_count_not_length():
    """A single long token is still one word, so still emphasis."""
    assert extract_quotes('a "' + "x" * 40 + '" term') == []
    assert extract_quotes('a "two words" term') == ["two words"]


def test_normalize_is_idempotent():
    s = "  It’s   a  TEST—really  "
    assert normalize(normalize(s)) == normalize(s)
    assert normalize(s) == "it's a test-really"


def test_works_with_evidence_objects_not_just_dicts():
    """Phase 4 will pass Evidence dataclasses straight in."""
    from app.retrieval import Evidence
    ev = [Evidence(
        source_id="s", source_title="T", speaker="Sp", source_url="u",
        transcript_id="t", chunk_id="c", publish_date=None, chunk_index=0,
        guest="G", text="Retention compounds when onboarding is excellent.",
        start_seconds=0, end_seconds=1, similarity=0.9, citation_url="u?t=0")]
    assert verify_answer(
        '"Retention compounds when onboarding is excellent." [E1]', ev).grounded
    assert not verify_answer('"A sentence nobody in the corpus ever said." [E1]',
                             ev).grounded
