"""Follow-up retrieval and session isolation -- eval case 4.

Session isolation is a correctness requirement, not a nicety. These tests
exercise it where it is easiest to break: a follow-up question carries almost
no retrievable signal on its own, so the system reaches for conversation
history -- and that is precisely the moment another session's context could
leak in.

Sessions are created in the same read-only-by-convention session that reads
the corpus, and the fixture rolls back on exit, so nothing persists. That
mirrors production, where conversation state and the transcript index live in
ONE database -- which is the whole point of the pgvector decision.
"""
from __future__ import annotations

import uuid

import pytest

from app import repository as repo
from app.retrieval import retrieve, retrieve_for_session

pytestmark = pytest.mark.usefixtures("corpus_ready", "ollama_ready")

FOLLOW_UP = "Why does that work so well?"


async def _seed(db, *turns: str) -> uuid.UUID:
    session = await repo.create_session(
        db, title="t", user_metadata={}, provider="ollama", model="test")
    for turn in turns:
        await repo.append_message(db, session.id, role="user", content=turn)
    # flush, never commit: the fixture rolls back so the dev
    # database is left exactly as it was found.
    await db.flush()
    return session.id


# --------------------------------------------------------------------------
# Case 4 -- follow-up resolves against session context
# --------------------------------------------------------------------------
async def test_bare_follow_up_retrieves_poorly_on_its_own(corpus_db):
    """Establishes the baseline the next test improves on."""
    evidence = await retrieve(corpus_db, FOLLOW_UP, k=3)
    # "Why does that work?" refers to nothing; it should not confidently
    # match transcript material.
    assert not evidence, f"unexpectedly matched: {[e.source_id for e in evidence]}"


async def test_follow_up_resolves_using_session_history(corpus_db):
    """With the prior turn, the same follow-up finds the right episode."""
    sid = await _seed(corpus_db, "How does Duolingo use streaks to improve retention?")
    evidence = await retrieve_for_session(corpus_db, sid, FOLLOW_UP, k=3)

    assert evidence, "follow-up found nothing even with context"
    assert "jackson-shuttleworth" in {e.source_id for e in evidence}


async def test_history_turns_zero_disables_context(corpus_db):
    sid = await _seed(corpus_db, "How does Duolingo use streaks to improve retention?")
    evidence = await retrieve_for_session(
        corpus_db, sid, FOLLOW_UP, k=3, history_turns=0)
    assert evidence == []


# --------------------------------------------------------------------------
# Session isolation under retrieval
# --------------------------------------------------------------------------
async def test_context_does_not_leak_between_sessions(corpus_db):
    """The core isolation guarantee, tested where it matters most.

    Two sessions ask the SAME bare follow-up. Their histories are about
    completely different episodes. Each must retrieve only its own subject.
    """
    a = await _seed(corpus_db, "How does Duolingo use streaks to improve retention?")
    b = await _seed(corpus_db, "How is SEO changing in the age of AI?")

    ev_a = await retrieve_for_session(corpus_db, a, FOLLOW_UP, k=3)
    ev_b = await retrieve_for_session(corpus_db, b, FOLLOW_UP, k=3)

    sources_a = {e.source_id for e in ev_a}
    sources_b = {e.source_id for e in ev_b}

    assert "jackson-shuttleworth" in sources_a
    assert "eli-schwartz" in sources_b
    assert "eli-schwartz" not in sources_a, "session B leaked into session A"
    assert "jackson-shuttleworth" not in sources_b, "session A leaked into B"


async def test_unknown_session_contributes_no_context(corpus_db):
    """A session id that does not exist must not crash or borrow context."""
    evidence = await retrieve_for_session(
        corpus_db, uuid.uuid4(), FOLLOW_UP, k=3)
    assert evidence == []


async def test_only_user_turns_are_used_as_context(corpus_db):
    """Assistant text must not steer retrieval.

    Otherwise a model's own wording feeds back into what it retrieves next,
    compounding an early mistake across a conversation.
    """
    session = await repo.create_session(
        corpus_db, title="t", user_metadata={}, provider="ollama", model="test")
    await repo.append_message(
        corpus_db, session.id, role="assistant",
        content="How is SEO changing in the age of AI? eli schwartz seo")
    await corpus_db.flush()

    evidence = await retrieve_for_session(corpus_db, session.id, FOLLOW_UP, k=3)
    assert evidence == [], "assistant turn was used as retrieval context"


async def test_only_recent_turns_are_used(corpus_db):
    """history_turns bounds how far back context reaches."""
    sid = await _seed(
        corpus_db,
        "How is SEO changing in the age of AI?",                # older
        "How does Duolingo use streaks to improve retention?",  # recent
    )
    evidence = await retrieve_for_session(
        corpus_db, sid, FOLLOW_UP, k=3, history_turns=1)

    sources = {e.source_id for e in evidence}
    assert "jackson-shuttleworth" in sources
    assert "eli-schwartz" not in sources, "reached past history_turns"
