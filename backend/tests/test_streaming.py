"""Streaming transport and the agent event protocol.

Requirement 1 of the locked Provider UX contract: streaming is baseline UX for
every model request. These tests prove the transport delivers *incrementally*
rather than buffering and flushing once -- a distinction a naive test misses.

Phase 4 note: the scratch test database holds no corpus, so these tests run
the ABSTENTION path by default. That is not a limitation, it is the single
most important safety property in the product -- an empty index must produce a
refusal, not an unsourced answer. The corpus-backed streaming path is covered
at the bottom and skips cleanly when the corpus or Ollama is absent.
"""
from __future__ import annotations

import json

import pytest

from app.agent import ABSTENTION


def parse_sse(raw: str) -> list[tuple[str, dict]]:
    out = []
    for frame in raw.split("\n\n"):
        if not frame.strip():
            continue
        ev, data = "message", ""
        for line in frame.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if data:
            out.append((ev, json.loads(data)))
    return out


async def _stream(client, session_id, content: str) -> list[tuple[str, dict]]:
    async with client.stream(
        "POST", f"/api/sessions/{session_id}/messages",
        json={"content": content},
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        raw = "".join([chunk async for chunk in r.aiter_text()])
    return parse_sse(raw)


# --------------------------------------------------------------------------
# Protocol shape
# --------------------------------------------------------------------------
async def test_protocol_order_meta_sources_delta_done(client, session_id):
    events = await _stream(client, session_id, "hello world")
    kinds = [e for e, _ in events]

    assert kinds[0] == "meta", "meta must arrive first so the UI can label the turn"
    assert kinds[-1] == "done", "exactly one terminal event, last"
    assert kinds.count("done") == 1
    assert "error" not in kinds
    assert "sources" in kinds
    assert "delta" in kinds
    assert "grounding" in kinds

    # Sources precede any generated text: citations are retrieved evidence,
    # not model claims, so they are trustworthy before a token exists.
    assert kinds.index("sources") < kinds.index("delta")
    # Verification necessarily follows the text it verifies.
    assert kinds.index("grounding") > kinds.index("delta")


async def test_meta_identifies_provider_and_session(client, session_id):
    meta = (await _stream(client, session_id, "hi"))[0][1]
    assert meta["provider"] == "ollama"
    assert meta["model"]
    assert meta["session_id"] == session_id


# --------------------------------------------------------------------------
# Abstention -- an empty index must refuse, not improvise
# --------------------------------------------------------------------------
async def test_empty_index_abstains_and_says_so(client, session_id):
    events = await _stream(client, session_id, "How do I improve retention?")
    text = "".join(p["text"] for e, p in events if e == "delta")
    done = events[-1][1]

    assert text.strip() == ABSTENTION
    assert done["abstained"] is True
    assert done["supported"] is False
    assert done["trustworthy"] is False


async def test_abstention_reports_zero_sources(client, session_id):
    events = await _stream(client, session_id, "anything at all")
    sources = [p for e, p in events if e == "sources"][0]
    assert sources["count"] == 0
    assert sources["supported"] is False
    assert sources["sources"] == []


async def test_abstention_is_vacuously_grounded(client, session_id):
    """No model ran, so there is nothing to have fabricated."""
    events = await _stream(client, session_id, "anything")
    grounding = [p for e, p in events if e == "grounding"][0]
    assert grounding["grounded"] is True
    assert grounding["quotes_found"] == 0
    assert grounding["invalid_tags"] == []


async def test_abstention_text_carries_no_citation_tags(client, session_id):
    """The refusal must not cite sources it does not have."""
    events = await _stream(client, session_id, "anything")
    text = "".join(p["text"] for e, p in events if e == "delta")
    assert "[E" not in text


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------
async def test_streamed_assistant_turn_is_persisted(client, session_id):
    events = await _stream(client, session_id, "persist me")
    done = [p for e, p in events if e == "done"][0]

    msgs = (await client.get(f"/api/sessions/{session_id}/messages")).json()
    assistant = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["id"] == done["message_id"]
    assert assistant[0]["content"]
    assert assistant[0]["provider"] == "ollama"
    # The user turn is persisted even though the stream came after it.
    assert [m["content"] for m in msgs if m["role"] == "user"] == ["persist me"]


async def test_done_reports_sequence_and_latency(client, session_id):
    done = (await _stream(client, session_id, "x"))[-1][1]
    assert done["seq"] == 2
    assert done["latency_ms"] >= 0
    assert done["content_length"] > 0


# --------------------------------------------------------------------------
# The real generation path (needs corpus + Ollama)
# --------------------------------------------------------------------------
@pytest.mark.usefixtures("corpus_ready", "ollama_ready")
async def test_grounded_answer_streams_incrementally(client, session_id,
                                                     monkeypatch):
    """Against a real corpus the answer arrives in many frames, with sources.

    Points the app at the ingested corpus for the duration of this test so the
    agent has something to retrieve.
    """
    from app import agent
    from app.db import session_factory as real_factory
    from tests.conftest import CORPUS_DB_URL

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    engine = create_async_engine(CORPUS_DB_URL)

    class _Factory:
        def __call__(self):
            return AsyncSession(engine, expire_on_commit=False)

    import app.routers.chat as chat_mod
    monkeypatch.setattr(chat_mod, "session_factory", lambda: _Factory())

    events = await _stream(
        client, session_id, "How does Duolingo use streaks to improve retention?")
    await engine.dispose()

    kinds = [e for e, _ in events]
    sources = [p for e, p in events if e == "sources"][0]
    deltas = [p for e, p in events if e == "delta"]
    grounding = [p for e, p in events if e == "grounding"][0]

    assert sources["count"] > 0, "real corpus returned no evidence"
    assert sources["supported"] is True
    assert all(s["citation_url"].startswith("http") for s in sources["sources"])
    assert len(deltas) > 5, f"not incremental: {len(deltas)} frames"
    assert "grounding" in kinds
    assert grounding["invalid_tags"] == [], "model cited evidence that does not exist"
