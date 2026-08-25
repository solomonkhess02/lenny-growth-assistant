"""Citations and verdicts must survive a reload.

Phase 5. Before this, `sources` and `grounding` were streamed and discarded:
reopening a session replayed the assistant's text with no evidence attached
and -- the part that actually matters -- with any FAILED verdict dropped, so a
retracted answer came back looking clean.

These tests pin the round trip: what the stream showed is what the database
returns.
"""
from __future__ import annotations

import pytest

from tests.test_streaming import _stream


async def _assistant_row(client, session_id: str) -> dict:
    msgs = (await client.get(f"/api/sessions/{session_id}/messages")).json()
    rows = [m for m in msgs if m["role"] == "assistant"]
    assert len(rows) == 1, f"expected one assistant turn, got {len(rows)}"
    return rows[0]


# --------------------------------------------------------------------------
# B3 -- abstention (the path the scratch database exercises by default)
# --------------------------------------------------------------------------
async def test_abstention_persists_empty_sources_and_a_clean_verdict(
        client, session_id):
    """No evidence, no model call -- but still an explicit recorded verdict."""
    events = await _stream(client, session_id, "How do I improve retention?")
    streamed = [p for e, p in events if e == "grounding"][0]

    row = await _assistant_row(client, session_id)
    assert row["sources"] == []
    assert row["grounding"] is not None, "a verdict was reached; record it"
    assert row["grounding"]["grounded"] is True
    assert row["grounding"]["verdict"] == streamed["verdict"]


async def test_user_turns_carry_no_verdict(client, session_id):
    """NULL means 'no verdict recorded'. A user turn has nothing to verify.

    This is why `grounding` is nullable rather than defaulting to a PASS-shaped
    dict: unverified and verified-clean must not be the same value.
    """
    await _stream(client, session_id, "hello")
    msgs = (await client.get(f"/api/sessions/{session_id}/messages")).json()
    user = [m for m in msgs if m["role"] == "user"][0]
    assert user["grounding"] is None
    assert user["sources"] == []


# --------------------------------------------------------------------------
# B1/B2 -- the generated path, with a stubbed provider so a verdict can be
# forced either way. No corpus or model required.
# --------------------------------------------------------------------------
@pytest.fixture
def forced_answer(monkeypatch):
    """Make the agent answer from fixed evidence with a chosen text."""
    from app import agent as agent_mod
    from app import providers as providers_mod
    from app.retrieval import Evidence

    evidence = [Evidence(
        source_id="jackson-shuttleworth", source_title="Duolingo streaks",
        speaker="Jackson Shuttleworth", source_url="https://youtu.be/x",
        transcript_id="t0", chunk_id="c0", publish_date=None, chunk_index=0,
        guest="Jackson Shuttleworth", text="Streaks work because of habit.",
        start_seconds=10, end_seconds=20, similarity=0.71,
        citation_url="https://youtu.be/x?t=10",
    )]

    async def _fake_retrieve(db, session_id, question, k):
        return evidence

    monkeypatch.setattr(agent_mod, "retrieve_for_session", _fake_retrieve)

    def _install(text: str):
        class FakeRuntime:
            def __init__(self, settings):
                pass

            def stream(self, **kwargs):
                async def gen():
                    for word in text.split(" "):
                        yield word + " "
                return gen()

        monkeypatch.setattr(providers_mod, "PiRuntime", FakeRuntime)

    return _install


async def test_grounded_answer_persists_its_citations(client, session_id,
                                                      forced_answer):
    """B1. The stored sources are the streamed sources, field for field."""
    forced_answer("Habit formation drives it. [E1]")
    events = await _stream(client, session_id, "why do streaks work?")
    streamed = [p for e, p in events if e == "sources"][0]["sources"]

    row = await _assistant_row(client, session_id)
    assert row["sources"] == streamed
    assert row["sources"][0]["label"] == "E1"
    assert row["sources"][0]["citation_url"] == "https://youtu.be/x?t=10"
    assert row["sources"][0]["source_title"] == "Duolingo streaks"
    assert row["grounding"]["verdict"] == "PASS"


async def test_failed_verdict_is_persisted_not_dropped(client, session_id,
                                                       forced_answer):
    """B2. The retraction has to survive the reload, or reload launders it.

    The model both fabricates a quote and cites evidence that does not exist.
    """
    forced_answer('He said "streaks are a golden goose" and more. [E7]')
    events = await _stream(client, session_id, "why do streaks work?")
    streamed = [p for e, p in events if e == "grounding"][0]
    assert streamed["grounded"] is False, "test premise: the stream must fail it"

    row = await _assistant_row(client, session_id)
    assert row["grounding"]["grounded"] is False
    assert row["grounding"]["verdict"] == "FAIL"
    assert row["grounding"]["invalid_tags"] == ["E7"]
    assert row["grounding"]["fabricated_quotes"] == streamed["fabricated_quotes"]
    # The citations it *did* have are still recorded -- a failed verdict does
    # not erase the evidence that was actually retrieved.
    assert len(row["sources"]) == 1
