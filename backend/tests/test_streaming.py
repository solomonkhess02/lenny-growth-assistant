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
# The SESSION decides the provider -- not LLM_PROVIDER, and not the request
# --------------------------------------------------------------------------
async def test_turn_runs_on_the_sessions_provider_not_the_configured_one(client):
    """A4. LLM_PROVIDER is ollama here; a deepseek session still says deepseek.

    Runs the abstention path, which never invokes the provider -- so this
    proves provider *selection* without needing a cloud credential.
    """
    from app.config import get_settings
    assert get_settings().llm_provider == "ollama", "test premise"

    sid = (await client.post("/api/sessions", json={"provider": "deepseek"})).json()["id"]
    meta = (await _stream(client, sid, "hi"))[0][1]

    assert meta["provider"] == "deepseek"
    assert meta["model"] == get_settings().deepseek_model


async def test_sessions_on_different_providers_do_not_contaminate_each_other(client):
    """Two live sessions, two providers, each turn stamped with its own."""
    a = (await client.post("/api/sessions", json={"provider": "ollama"})).json()["id"]
    b = (await client.post("/api/sessions", json={"provider": "deepseek"})).json()["id"]

    assert (await _stream(client, a, "x"))[0][1]["provider"] == "ollama"
    assert (await _stream(client, b, "x"))[0][1]["provider"] == "deepseek"
    # And again, to prove the first stream did not rebind anything global.
    assert (await _stream(client, a, "y"))[0][1]["provider"] == "ollama"


async def test_a_failing_provider_is_surfaced_never_substituted(client, monkeypatch):
    """A6. The single most important provider property.

    A dead provider must end the stream with an error. It must NEVER be
    quietly swapped for the one that happens to be working -- an answer the
    user believes came from the model they chose, produced by another model,
    is a lie the UI cannot detect.
    """
    from app import agent as agent_mod
    from app import providers as providers_mod
    from app.errors import ProviderUnavailable
    from app.retrieval import Evidence

    async def _fake_retrieve(db, session_id, question, k):
        # Evidence exists, so the agent proceeds to generation -- which is
        # where the provider failure has to happen for this test to mean
        # anything. With no evidence it would abstain and never call it.
        return [Evidence(
            source_id="s", source_title="T", speaker="S",
            source_url="https://youtu.be/x", transcript_id="t", chunk_id="c",
            publish_date=None, chunk_index=0, guest="G", text="Some evidence.",
            start_seconds=0, end_seconds=5, similarity=0.9,
            citation_url="https://youtu.be/x?t=0",
        )]

    class DeadRuntime:
        def __init__(self, settings):
            pass

        def stream(self, **kwargs):
            async def gen():
                raise ProviderUnavailable(
                    "The selected model provider is not reachable.")
                yield ""  # pragma: no cover - unreachable, defines a generator
            return gen()

    monkeypatch.setattr(agent_mod, "retrieve_for_session", _fake_retrieve)
    monkeypatch.setattr(providers_mod, "PiRuntime", DeadRuntime)

    sid = (await client.post("/api/sessions", json={"provider": "deepseek"})).json()["id"]
    events = await _stream(client, sid, "How do I improve retention?")
    kinds = [e for e, _ in events]

    assert kinds[-1] == "error", f"stream did not end in an error: {kinds}"
    err = events[-1][1]
    assert err["code"] == "provider_unavailable"
    assert err["retryable"] is True
    assert "done" not in kinds, "a failed turn must not also report success"

    # Nothing anywhere in the stream may name the other provider.
    assert "ollama" not in json.dumps(events), (
        "the failing provider appears to have been substituted")


async def test_a_generation_timeout_ends_the_stream_in_a_retryable_error(
        client, session_id, monkeypatch):
    """Phase 8. `GenerationTimeout` (raised by pi_runtime past its idle/total
    bound -- proven directly in test_pi_runtime.py) must reach the client as
    a proper terminal error frame, exactly like any other provider failure:
    no `done`, no fabricated `grounding`, and marked retryable.
    """
    from app import agent as agent_mod
    from app import providers as providers_mod
    from app.errors import GenerationTimeout
    from app.retrieval import Evidence

    async def _fake_retrieve(db, session_id, question, k):
        return [Evidence(
            source_id="s", source_title="T", speaker="S",
            source_url="https://youtu.be/x", transcript_id="t", chunk_id="c",
            publish_date=None, chunk_index=0, guest="G", text="Some evidence.",
            start_seconds=0, end_seconds=5, similarity=0.9,
            citation_url="https://youtu.be/x?t=0",
        )]

    class HangingRuntime:
        def __init__(self, settings):
            pass

        def stream(self, **kwargs):
            async def gen():
                yield "Some partial text, then nothing more ever arrives. "
                raise GenerationTimeout(
                    "Provider 'ollama' produced no output for 120s.")
            return gen()

    monkeypatch.setattr(agent_mod, "retrieve_for_session", _fake_retrieve)
    monkeypatch.setattr(providers_mod, "PiRuntime", HangingRuntime)

    events = await _stream(client, session_id, "How do I improve retention?")
    kinds = [e for e, _ in events]

    assert kinds[-1] == "error", f"stream did not end in an error: {kinds}"
    err = events[-1][1]
    assert err["code"] == "generation_timeout"
    assert err["retryable"] is True
    assert "grounding" not in kinds, (
        "a timeout must not carry a verdict -- the text was never verified")
    assert "done" not in kinds


async def test_a_provider_failure_after_partial_output_emits_no_grounding(
        client, session_id, monkeypatch):
    """D3 (Phase 8). Partial text must never carry an invented verdict.

    If the provider dies mid-generation, `verify_answer` must never run --
    otherwise there would be no way to tell "never checked" from "checked and
    passed", and the client would have no signal to stop the partial text
    from rendering exactly like a normal, trustworthy answer.
    """
    from app import agent as agent_mod
    from app import providers as providers_mod
    from app.errors import ProviderUnavailable
    from app.retrieval import Evidence

    async def _fake_retrieve(db, session_id, question, k):
        return [Evidence(
            source_id="s", source_title="T", speaker="S",
            source_url="https://youtu.be/x", transcript_id="t", chunk_id="c",
            publish_date=None, chunk_index=0, guest="G", text="Some evidence.",
            start_seconds=0, end_seconds=5, similarity=0.9,
            citation_url="https://youtu.be/x?t=0",
        )]

    class DyingMidStreamRuntime:
        def __init__(self, settings):
            pass

        def stream(self, **kwargs):
            async def gen():
                yield "Partial answer text before the provider dies. "
                raise ProviderUnavailable("Connection reset mid-generation.")
            return gen()

    monkeypatch.setattr(agent_mod, "retrieve_for_session", _fake_retrieve)
    monkeypatch.setattr(providers_mod, "PiRuntime", DyingMidStreamRuntime)

    events = await _stream(client, session_id, "How do I improve retention?")
    kinds = [e for e, _ in events]

    assert "delta" in kinds, "test premise: some text must have streamed"
    assert kinds[-1] == "error"
    assert "grounding" not in kinds, (
        "verify_answer must never run on an incomplete answer -- a verdict "
        "here would let partial text be mistaken for a checked one")
    assert "done" not in kinds

    err = events[-1][1]
    assert err["code"] == "provider_unavailable"
    assert err["retryable"] is True
    # Correlatable with the meta frame's request id, and with server logs.
    meta = events[0][1]
    assert err["request_id"], "error frame must carry a request_id"
    assert err["request_id"] == meta["request_id"]


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


# --------------------------------------------------------------------------
# D1 (Phase 8) -- cancellation must be logged and must keep propagating
# --------------------------------------------------------------------------
async def test_cancellation_mid_stream_is_logged_and_reraised(
        session_id, monkeypatch, caplog):
    """If cancellation reaches the generator (Starlette's own disconnect
    handling winning the race against the `is_disconnected()` poll), it
    must be LOGGED, not silently discarded, and must keep propagating --
    swallowing it would leave the underlying task never actually cancelled.

    Drives the router's real generator directly via `response.body_iterator`
    rather than through the HTTP client: under `ASGITransport`,
    `is_disconnected()` never resolves true during an active stream (its
    `receive()` blocks on the response completing first), so a real
    cancellation cannot be provoked end-to-end through `client.stream(...)`
    -- confirmed while auditing this failure mode for Phase 8.
    """
    import asyncio
    import uuid as uuid_mod

    from app import agent as agent_mod
    from app.db import session_factory
    from app.routers.chat import post_message
    from app.schemas import MessageCreate

    async def _fake_retrieve(db, session_id, question, k):
        return []  # abstention: the shortest path to a real in-try yield

    monkeypatch.setattr(agent_mod, "retrieve_for_session", _fake_retrieve)

    class _NeverDisconnected:
        async def is_disconnected(self):
            return False

    async with session_factory()() as db:
        response = await post_message(
            uuid_mod.UUID(session_id), MessageCreate(content="hi"),
            _NeverDisconnected(), db=db)

    body = response.body_iterator
    with caplog.at_level("INFO", logger="app.chat"):
        await body.asend(None)  # "meta" -- yielded BEFORE the try block opens
        await body.asend(None)  # "sources" -- now suspended INSIDE the try
        with pytest.raises(asyncio.CancelledError):
            await body.athrow(asyncio.CancelledError())

    assert any(
        r.message == "stream_cancelled" and getattr(r, "outcome", None) == "cancelled"
        for r in caplog.records
    ), "cancellation must be logged, not silently discarded"


async def test_stream_answer_closes_the_provider_stream_on_early_close(monkeypatch):
    """D1 (Phase 8), the agent-layer half of the fix.

    Closing `stream_answer` early -- as the router's `aclosing(...)` does on
    a disconnect or cancellation -- must propagate the close down to
    whatever `provider.stream()` returned. Before this, `stream_answer` had
    no `try/finally` at all, so closing it depended on the event loop's
    async-generator finalizer eventually collecting the frame: neither
    synchronous with the disconnect nor guaranteed to run at all, and until
    it did the provider (in production, Pi's subprocess) kept generating,
    unobserved, as an orphan. `stub-based, no corpus/Ollama needed -- kept
    here rather than in test_agent.py, which gates the whole module on both.
    """
    from app import agent as agent_mod
    from app.retrieval import Evidence

    closed = {"value": False}

    class TrackingProvider:
        name = "ollama"
        model = "m"

        def stream(self, prompt, **kwargs):
            async def gen():
                try:
                    yield "hello "
                    yield "world"
                finally:
                    closed["value"] = True
            return gen()

    async def _fake_retrieve(db, question, k):
        return [Evidence(
            source_id="s", source_title="T", speaker="S",
            source_url="https://youtu.be/x", transcript_id="t", chunk_id="c",
            publish_date=None, chunk_index=0, guest="G", text="Some evidence.",
            start_seconds=0, end_seconds=5, similarity=0.9,
            citation_url="https://youtu.be/x?t=0")]

    monkeypatch.setattr(agent_mod, "retrieve", _fake_retrieve)

    gen = agent_mod.stream_answer(
        None, "q", session_id=None, provider=TrackingProvider())
    kind, _ = await gen.__anext__()
    assert kind == "sources"
    kind, _ = await gen.__anext__()
    assert kind == "delta"  # exactly one delta consumed -- the stream is
                             # NOT exhausted; a second delta is still pending

    await gen.aclose()
    assert closed["value"] is True, (
        "stream_answer must close the underlying provider stream when "
        "closed early, not leave it running")
