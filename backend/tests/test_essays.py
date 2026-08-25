"""The essay HTTP surface: entry conditions, provenance, persistence.

The guards here are the reason this file exists. A hidden button is not an
access control, and the rule that matters most -- **no essay is written from an
answer that failed verification** -- has to hold for any caller, not just the
one using our UI. Each rejection below is a case where producing 1,250
confident words would give a reader something they should not be given.

Runs against the scratch test database, whose index is empty, so answers here
take the abstention path unless a turn is seeded directly. That is convenient
rather than limiting: seeding lets a test state exactly which verdict a source
answer carried, which is the variable under test.
"""
from __future__ import annotations

import json
import uuid

import pytest

from tests.test_streaming import parse_sse


async def _seed_answer(client, session_id: str, *, grounded: bool = True,
                       sources: list[dict] | None = None,
                       role: str = "assistant") -> str:
    """Write one turn straight to the database and return its id.

    Direct insertion rather than a real stream: this file is about what the
    ESSAY route does with a stored turn, and seeding is the only way to pin the
    verdict and the citations a test needs without a live model.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    from app import repository as repo
    from app.db import engine

    default_sources = [{
        "label": "E1", "source_id": "ep", "source_title": "An Episode",
        "guest": "G", "speaker": "S", "citation_url": "https://youtu.be/x?t=0",
        "start_seconds": 0, "publish_date": None, "similarity": 0.8,
        "chunk_id": str(uuid.uuid4()), "transcript_id": str(uuid.uuid4()),
    }]
    verdict = {
        "verdict": "PASS" if grounded else "FAIL", "grounded": grounded,
        "quotes_found": 1, "fabricated_quotes": [] if grounded else ["invented"],
        "tags_found": ["E1"], "invalid_tags": [] if grounded else ["E9"],
    }

    async with AsyncSession(engine(), expire_on_commit=False) as db:
        await repo.append_message(db, uuid.UUID(session_id),
                                  role="user", content="a question")
        msg = await repo.append_message(
            db, uuid.UUID(session_id), role=role, content="A prior answer.",
            provider="ollama", model="qwen3:4b-instruct", latency_ms=10,
            sources=default_sources if sources is None else sources,
            grounding=verdict,
        )
        await db.commit()
        return str(msg.id)


async def _post_essay(client, session_id: str, message_id: str):
    async with client.stream(
        "POST", f"/api/sessions/{session_id}/essays",
        json={"source_message_id": message_id},
    ) as r:
        status = r.status_code
        raw = "".join([chunk async for chunk in r.aiter_text()])
    return status, raw


# --------------------------------------------------------------------------
# Entry conditions -- enforced server-side, for every caller
# --------------------------------------------------------------------------
async def test_unknown_session_is_404(client):
    r = await client.post(f"/api/sessions/{uuid.uuid4()}/essays",
                          json={"source_message_id": str(uuid.uuid4())})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_unknown_message_is_404(client, session_id):
    r = await client.post(f"/api/sessions/{session_id}/essays",
                          json={"source_message_id": str(uuid.uuid4())})
    assert r.status_code == 404


async def test_message_from_another_session_is_404_not_403(client):
    """Reported absent, not forbidden.

    A 403 would confirm the id exists somewhere, which is a fact about another
    session that this one is not entitled to learn.
    """
    a = (await client.post("/api/sessions", json={})).json()["id"]
    b = (await client.post("/api/sessions", json={})).json()["id"]
    msg_id = await _seed_answer(client, a)

    r = await client.post(f"/api/sessions/{b}/essays",
                          json={"source_message_id": msg_id})
    assert r.status_code == 404
    assert "not_found" == r.json()["error"]["code"]


async def test_user_turn_cannot_be_turned_into_an_essay(client, session_id):
    msg_id = await _seed_answer(client, session_id, role="user")
    r = await client.post(f"/api/sessions/{session_id}/essays",
                          json={"source_message_id": msg_id})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_failed"


async def test_abstention_cannot_be_turned_into_an_essay(client, session_id):
    """G3. No evidence means the model was never invoked.

    Writing an essay anyway would mean writing it from the model's memory --
    exactly what "no evidence, no answer" exists to prevent, laundered into a
    longer artifact.
    """
    msg_id = await _seed_answer(client, session_id, sources=[])
    r = await client.post(f"/api/sessions/{session_id}/essays",
                          json={"source_message_id": msg_id})
    assert r.status_code == 422
    assert "abstention" in r.json()["error"]["message"].lower()


async def test_failed_verdict_cannot_be_turned_into_an_essay(client, session_id):
    """G4. The single most important guard in this file.

    An answer already known to contain fabricated quotes must not become the
    foundation of 1,250 more confident words.
    """
    msg_id = await _seed_answer(client, session_id, grounded=False)
    r = await client.post(f"/api/sessions/{session_id}/essays",
                          json={"source_message_id": msg_id})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"
    assert "verification" in r.json()["error"]["message"]


async def test_unverified_answer_is_refused_like_a_failed_one(client, session_id):
    """NULL grounding is not a PASS, and this is where that has to hold."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app import repository as repo
    from app.db import engine

    async with AsyncSession(engine(), expire_on_commit=False) as db:
        msg = await repo.append_message(
            db, uuid.UUID(session_id), role="assistant", content="Unverified.",
            provider="ollama", model="m",
            sources=[{"label": "E1", "chunk_id": str(uuid.uuid4())}],
            grounding=None,
        )
        await db.commit()
        msg_id = str(msg.id)

    r = await client.post(f"/api/sessions/{session_id}/essays",
                          json={"source_message_id": msg_id})
    assert r.status_code == 409
    assert "no recorded verification" in r.json()["error"]["message"]


async def test_stale_chunk_ids_end_the_stream_with_evidence_unavailable(
        client, session_id):
    """G6/E3. Re-ingest replaces chunk ids; the essay refuses rather than
    silently searching for different material to write from."""
    msg_id = await _seed_answer(client, session_id)   # random, unresolvable ids
    status, raw = await _post_essay(client, session_id, msg_id)

    assert status == 200, "the failure is a terminal EVENT, not an HTTP error"
    events = parse_sse(raw)
    kinds = [e for e, _ in events]
    assert kinds[-1] == "error"
    assert events[-1][1]["code"] == "evidence_unavailable"
    assert "done" not in kinds, "a failed generation must not also report success"


# --------------------------------------------------------------------------
# The request cannot smuggle in a provider, or evidence
# --------------------------------------------------------------------------
def test_essay_request_carries_only_a_message_id():
    """P2. Structural: the shape makes an override unrepresentable.

    Everything else the generator needs is read from the stored turn, so a
    client cannot supply evidence the system never retrieved.
    """
    from app.schemas import EssayCreate
    assert set(EssayCreate.model_fields) == {"source_message_id"}


async def test_provider_in_the_body_is_ignored(client, session_id):
    msg_id = await _seed_answer(client, session_id)
    status, raw = await _post_essay_with_extra(
        client, session_id, msg_id, {"provider": "deepseek"})

    assert status == 200
    events = parse_sse(raw)
    meta = events[0][1]
    assert meta["provider"] == "ollama", "a body field overrode the session"
    assert "deepseek" not in json.dumps(events)


async def _post_essay_with_extra(client, session_id, message_id, extra: dict):
    body = {"source_message_id": message_id, **extra}
    async with client.stream(
        "POST", f"/api/sessions/{session_id}/essays", json=body
    ) as r:
        status = r.status_code
        raw = "".join([chunk async for chunk in r.aiter_text()])
    return status, raw


async def test_essay_runs_on_the_sessions_provider_not_the_configured_one(client):
    """P1. LLM_PROVIDER is ollama here; a deepseek session still says deepseek."""
    from app.config import get_settings
    assert get_settings().llm_provider == "ollama", "test premise"

    sid = (await client.post("/api/sessions", json={"provider": "deepseek"})).json()["id"]
    msg_id = await _seed_answer(client, sid)
    _, raw = await _post_essay(client, sid, msg_id)

    meta = parse_sse(raw)[0][1]
    assert meta["provider"] == "deepseek"
    assert meta["model"] == get_settings().deepseek_model


async def test_meta_names_the_skill_that_will_write_the_essay(client, session_id):
    """S6. Skill 03: agent execution must expose the selected skill."""
    msg_id = await _seed_answer(client, session_id)
    _, raw = await _post_essay(client, session_id, msg_id)

    meta = parse_sse(raw)[0][1]
    assert meta["skill"] == "05-ship30-writing"
    assert len(meta["skill_sha256"]) == 64
    assert meta["kind"] == "essay"


# --------------------------------------------------------------------------
# Non-substitution -- the mirror of the Phase 5 chat test
# --------------------------------------------------------------------------
async def test_a_failing_provider_is_surfaced_never_substituted(client, monkeypatch):
    """P3. A dead provider ends the essay stream in an error.

    It must NEVER be quietly swapped for the one that happens to be working.
    An essay the reader believes came from the model they chose, written by
    another model, is a lie the UI cannot detect -- and an essay is a far more
    shareable artifact than a chat turn, so it travels further.
    """
    from app import providers as providers_mod
    from app import ship30 as ship30_mod
    from app.errors import ProviderUnavailable
    from app.retrieval import Evidence

    async def _fake_assemble(db, *, stored_sources, question, k=None):
        # Evidence exists, so generation is reached -- which is where the
        # provider failure has to happen for this test to mean anything.
        ev = [Evidence(
            source_id="s", source_title="T", speaker="S",
            source_url="https://youtu.be/x", transcript_id="t", chunk_id="c",
            publish_date=None, chunk_index=0, guest="G", text="Some evidence.",
            start_seconds=0, end_seconds=5, similarity=0.9,
            citation_url="https://youtu.be/x?t=0")]
        return ship30_mod.EssayEvidence(items=ev, carried=1)

    class DeadRuntime:
        def __init__(self, settings):
            pass

        def stream(self, **kwargs):
            async def gen():
                raise ProviderUnavailable(
                    "The selected model provider is not reachable.")
                yield ""  # pragma: no cover - defines a generator
            return gen()

    monkeypatch.setattr(ship30_mod, "assemble_evidence", _fake_assemble)
    monkeypatch.setattr(providers_mod, "PiRuntime", DeadRuntime)

    sid = (await client.post("/api/sessions", json={"provider": "deepseek"})).json()["id"]
    msg_id = await _seed_answer(client, sid)
    _, raw = await _post_essay(client, sid, msg_id)

    events = parse_sse(raw)
    kinds = [e for e, _ in events]
    assert kinds[-1] == "error", f"stream did not end in an error: {kinds}"
    assert events[-1][1]["code"] == "provider_unavailable"
    assert events[-1][1]["retryable"] is True
    assert "done" not in kinds

    # Nothing anywhere in the stream may name the other provider.
    assert "ollama" not in json.dumps(events), (
        "the failing provider appears to have been substituted")


# --------------------------------------------------------------------------
# Persistence and replay
# --------------------------------------------------------------------------
@pytest.fixture
def stub_generation(monkeypatch):
    """Make the essay path produce a known essay without a model.

    Returns a setter so each test states the exact output it needs.
    """
    from app import ship30 as ship30_mod
    from app.retrieval import Evidence

    text = {"markdown": "# A Title\n\nOne two three four five. [E1]\n"}

    ev = [Evidence(
        source_id="ep", source_title="An Episode", speaker="S",
        source_url="https://youtu.be/x", transcript_id="t", chunk_id="c",
        publish_date=None, chunk_index=0, guest="G",
        text="Streaks work because loss aversion is powerful.",
        start_seconds=0, end_seconds=5, similarity=0.8,
        citation_url="https://youtu.be/x?t=0")]

    async def _fake_assemble(db, *, stored_sources, question, k=None):
        return ship30_mod.EssayEvidence(items=ev, carried=1)

    class Stub:
        name = "ollama"
        model = "qwen3:4b-instruct"

        def stream(self, prompt, *, system_prompt=None, append_system_prompt=None):
            async def gen():
                for word in text["markdown"].split(" "):
                    yield word + " "
            return gen()

    monkeypatch.setattr(ship30_mod, "assemble_evidence", _fake_assemble)
    monkeypatch.setattr("app.routers.essays.get_provider", lambda name=None: Stub())
    return text


async def test_essay_is_persisted_with_full_provenance(client, session_id,
                                                       stub_generation):
    """P4/D1. Everything needed to attribute the essay later."""
    msg_id = await _seed_answer(client, session_id)
    _, raw = await _post_essay(client, session_id, msg_id)

    done = parse_sse(raw)[-1][1]
    assert done["essay_id"]

    r = await client.get(f"/api/essays/{done['essay_id']}")
    assert r.status_code == 200
    essay = r.json()

    assert essay["provider"] == "ollama"
    assert essay["model"] == "qwen3:4b-instruct"
    assert essay["latency_ms"] is not None
    assert essay["skill_name"] == "05-ship30-writing"
    assert len(essay["skill_sha256"]) == 64
    assert essay["source_message_id"] == msg_id
    assert essay["format"] == "markdown"
    assert essay["title"] == "A Title"


async def test_persisted_word_count_is_the_reported_one(client, session_id,
                                                        stub_generation):
    """W1. One number, from one definition, in both places."""
    from app.ship30 import word_count

    msg_id = await _seed_answer(client, session_id)
    _, raw = await _post_essay(client, session_id, msg_id)
    done = parse_sse(raw)[-1][1]

    essay = (await client.get(f"/api/essays/{done['essay_id']}")).json()
    assert essay["word_count"] == done["word_count"]
    assert essay["word_count"] == word_count(essay["markdown"])


async def test_citations_and_verdict_survive_a_reload(client, session_id,
                                                      stub_generation):
    """D1. The essay comes back looking exactly as it did live."""
    msg_id = await _seed_answer(client, session_id)
    _, raw = await _post_essay(client, session_id, msg_id)
    events = parse_sse(raw)
    streamed_sources = [p for e, p in events if e == "sources"][0]["sources"]
    streamed_verdict = [p for e, p in events if e == "grounding"][0]
    done = events[-1][1]

    essay = (await client.get(f"/api/essays/{done['essay_id']}")).json()
    assert essay["sources"] == streamed_sources
    assert essay["grounding"] == streamed_verdict


async def test_failed_verdict_on_an_essay_is_persisted_not_dropped(
        client, session_id, stub_generation):
    """A retracted essay must still read as retracted after a refresh."""
    stub_generation["markdown"] = (
        '# T\n\nHe said "a sentence in no transcript anywhere". [E9]\n')

    msg_id = await _seed_answer(client, session_id)
    _, raw = await _post_essay(client, session_id, msg_id)
    done = parse_sse(raw)[-1][1]

    assert done["trustworthy"] is False
    essay = (await client.get(f"/api/essays/{done['essay_id']}")).json()
    assert essay["grounding"]["grounded"] is False
    assert essay["grounding"]["fabricated_quotes"]
    assert essay["grounding"]["invalid_tags"] == ["E9"]
    # Stored, not deleted: a retracted essay stays inspectable.
    assert essay["markdown"]


async def test_essays_are_listed_per_session(client, session_id, stub_generation):
    msg_id = await _seed_answer(client, session_id)
    await _post_essay(client, session_id, msg_id)
    await _post_essay(client, session_id, msg_id)

    r = await client.get(f"/api/sessions/{session_id}/essays")
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_essays_do_not_appear_in_the_conversation(client, session_id,
                                                        stub_generation):
    """D4. An essay is an artifact, not a turn.

    If it were a message it would land in the transcript and in the history
    that `retrieve_for_session` builds follow-up queries from -- a 1,250-word
    turn quietly steering the next retrieval.
    """
    msg_id = await _seed_answer(client, session_id)
    await _post_essay(client, session_id, msg_id)

    msgs = (await client.get(f"/api/sessions/{session_id}/messages")).json()
    assert all("A Title" not in m["content"] for m in msgs)
    assert [m["role"] for m in msgs] == ["user", "assistant"]


async def test_essays_are_isolated_between_sessions(client, stub_generation):
    a = (await client.post("/api/sessions", json={})).json()["id"]
    b = (await client.post("/api/sessions", json={})).json()["id"]
    msg_id = await _seed_answer(client, a)
    await _post_essay(client, a, msg_id)

    assert len((await client.get(f"/api/sessions/{a}/essays")).json()) == 1
    assert (await client.get(f"/api/sessions/{b}/essays")).json() == []


async def test_deleting_a_session_cascades_its_essays(client, session_id,
                                                      stub_generation):
    """D2. No artifact outlives the conversation it cites."""
    msg_id = await _seed_answer(client, session_id)
    _, raw = await _post_essay(client, session_id, msg_id)
    essay_id = parse_sse(raw)[-1][1]["essay_id"]

    assert (await client.delete(f"/api/sessions/{session_id}")).status_code == 204
    assert (await client.get(f"/api/essays/{essay_id}")).status_code == 404


async def test_unknown_essay_is_structured_404(client):
    r = await client.get(f"/api/essays/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_protocol_order_matches_the_chat_protocol(client, session_id,
                                                        stub_generation):
    """The essay inherits the locked contract rather than inventing one."""
    msg_id = await _seed_answer(client, session_id)
    _, raw = await _post_essay(client, session_id, msg_id)
    kinds = [e for e, _ in parse_sse(raw)]

    assert kinds[0] == "meta"
    assert kinds[-1] == "done"
    assert kinds.count("done") == 1
    # Citations before text; verification after it. Same reasons as chat.
    assert kinds.index("sources") < kinds.index("delta")
    assert kinds.index("grounding") > kinds.index("delta")
