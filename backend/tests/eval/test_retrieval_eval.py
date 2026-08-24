"""Skill-02 retrieval evaluation -- cases 1, 2, 3, 6 (4 and 5 live in
`test_retrieval_session.py` and `test_retrieval.py`).

Driven by the PRE-REGISTERED question set in `calibration_set.json`, which was
committed before any score was observed (commit 87538dd). These tests assert
the behaviour that calibration measured; they do not get to move the line if
they fail.

The thresholds asserted here are the measured ones. Where the system is
imperfect -- two of sixteen supported questions do not surface their expected
episode -- the test records the real number rather than being relaxed until
it passes. See docs/retrieval-calibration.md.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import get_settings
from app.retrieval import retrieve

pytestmark = pytest.mark.usefixtures("corpus_ready", "ollama_ready")

SET = json.loads((Path(__file__).parent / "calibration_set.json")
                 .read_text(encoding="utf-8"))
QUESTIONS = SET["questions"]
SUPPORTED = [q for q in QUESTIONS if q["label"] == "supported"]
UNSUPPORTED = [q for q in QUESTIONS if q["label"] == "unsupported"]
NEAR_MISS = [q for q in QUESTIONS if q.get("kind") == "near_miss"]
OFF_DOMAIN = [q for q in QUESTIONS if q.get("kind") == "off_domain"]


def _ids(items):
    return [q["id"] for q in items]


# --------------------------------------------------------------------------
# The frozen set is what it claims to be
# --------------------------------------------------------------------------
def test_calibration_set_is_unrevised():
    """If someone edits the set post-hoc, they must record it as a revision."""
    assert SET["revisions"] == [], (
        "the calibration set has revisions; re-run the calibration and update "
        "docs/retrieval-calibration.md before trusting these thresholds")


def test_calibration_set_matches_the_deployed_configuration():
    settings = get_settings()
    assert SET["embedding_model"] == settings.embedding_model
    assert SET["embedding_dim"] == settings.embedding_dim


def test_set_has_enough_of_each_class():
    assert len(SUPPORTED) >= 12
    assert len(UNSUPPORTED) >= 8
    assert len(NEAR_MISS) >= 4, "near-miss questions are the honest test"


# --------------------------------------------------------------------------
# Case 1 -- answerable questions return evidence
# --------------------------------------------------------------------------
@pytest.mark.parametrize("q", SUPPORTED, ids=_ids(SUPPORTED))
async def test_answerable_question_returns_evidence(corpus_db, q):
    evidence = await retrieve(corpus_db, q["question"], k=3)
    assert evidence, f"{q['id']} returned no evidence: {q['question']!r}"
    assert evidence[0].similarity >= get_settings().retrieval_min_similarity


async def test_expected_episode_is_usually_retrieved(corpus_db):
    """Attribution accuracy, reported as measured -- 14/16 in top-3.

    The two misses (S11 product managers at startups, S16 layoffs) are real
    retrieval failures of all-minilm on this corpus. They are recorded here
    rather than papered over, and they are the standing argument for the
    documented nomic-embed-text upgrade path.
    """
    hits, misses = 0, []
    for q in SUPPORTED:
        evidence = await retrieve(corpus_db, q["question"], k=3)
        if q["expected_source_id"] in {e.source_id for e in evidence}:
            hits += 1
        else:
            misses.append(q["id"])

    assert hits >= 14, (
        f"attribution accuracy regressed to {hits}/{len(SUPPORTED)}; "
        f"missed {misses}")
    assert set(misses) <= {"S11", "S16"}, f"NEW attribution misses: {misses}"


# --------------------------------------------------------------------------
# Case 3 -- unsupported questions return NOTHING
# --------------------------------------------------------------------------
@pytest.mark.parametrize("q", UNSUPPORTED, ids=_ids(UNSUPPORTED))
async def test_unsupported_question_returns_no_evidence(corpus_db, q):
    """Silence is the correct answer.

    skill 02: when evidence is insufficient, say the transcript material does
    not support the answer. Returning the least-bad chunk instead is how a
    grounded system starts fabricating.
    """
    evidence = await retrieve(corpus_db, q["question"], k=3)
    assert evidence == [], (
        f"{q['id']} ({q.get('kind')}) wrongly matched "
        f"{[(e.source_id, e.similarity) for e in evidence]}: {q['question']!r}")


# --------------------------------------------------------------------------
# Case 6 -- irrelevant retrieval
# --------------------------------------------------------------------------
@pytest.mark.parametrize("q", OFF_DOMAIN, ids=_ids(OFF_DOMAIN))
async def test_off_domain_scores_far_below_the_floor(corpus_db, q):
    """Off-domain questions must not merely fail -- they must fail clearly."""
    evidence = await retrieve(corpus_db, q["question"], k=1,
                              min_similarity=-1.0)
    assert evidence
    top = evidence[0].similarity
    floor = get_settings().retrieval_min_similarity
    assert top < floor - 0.05, (
        f"{q['id']} scored {top:.4f}, uncomfortably close to the floor {floor}")


async def test_near_miss_questions_stay_below_the_floor(corpus_db):
    """The thin margin, asserted honestly.

    Near-miss questions topped out at 0.3811 against a floor of 0.40 -- a
    margin of ~0.019. This test exists to fail loudly if that gap closes,
    because it is the least robust number in Phase 3.
    """
    floor = get_settings().retrieval_min_similarity
    worst = 0.0
    for q in NEAR_MISS:
        evidence = await retrieve(corpus_db, q["question"], k=1,
                                  min_similarity=-1.0)
        worst = max(worst, evidence[0].similarity if evidence else 0.0)

    assert worst < floor, (
        f"a near-miss question scored {worst:.4f} at or above the floor "
        f"{floor}: the separating gap has closed and the floor needs "
        f"re-calibration against a revised question set")


# --------------------------------------------------------------------------
# Case 2 -- ambiguous questions draw on several sources
# --------------------------------------------------------------------------
@pytest.mark.parametrize("question", [
    "How do you grow a product?",
    "What metrics matter most?",
    "How should teams work together?",
])
async def test_ambiguous_question_spreads_across_sources(corpus_db, question):
    """A broad question has no single right episode.

    Concentrating a vague question on one source would present one guest's
    opinion as the field's consensus.
    """
    evidence = await retrieve(corpus_db, question, k=3, min_similarity=-1.0)
    assert len({e.source_id for e in evidence}) >= 2, (
        f"{question!r} collapsed onto {[e.source_id for e in evidence]}")


# --------------------------------------------------------------------------
# Headline metric
# --------------------------------------------------------------------------
async def test_supported_unsupported_classification_is_perfect(corpus_db):
    """The floor's whole job, measured on the frozen set."""
    wrong = []
    for q in QUESTIONS:
        evidence = await retrieve(corpus_db, q["question"], k=3)
        predicted = "supported" if evidence else "unsupported"
        if predicted != q["label"]:
            wrong.append((q["id"], q["label"], predicted))

    assert not wrong, f"misclassified {len(wrong)}/{len(QUESTIONS)}: {wrong}"
