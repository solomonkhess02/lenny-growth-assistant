"""Session lifecycle, message persistence, and — critically — isolation."""
from __future__ import annotations

import uuid

import pytest


async def test_create_session_records_provider_and_model(client):
    r = await client.post("/api/sessions", json={"title": "growth", "user_metadata": {"a": 1}})
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "growth"
    assert body["user_metadata"] == {"a": 1}
    # §3.2: the provider in force is persisted, not inferred later.
    assert body["provider"] == "ollama"
    assert body["model"]
    uuid.UUID(body["id"])


async def test_get_unknown_session_is_structured_404(client):
    r = await client.get(f"/api/sessions/{uuid.uuid4()}")
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "not_found"
    assert err["retryable"] is False
    assert "request_id" in err


async def test_malformed_uuid_is_422_not_500(client):
    r = await client.get("/api/sessions/not-a-uuid")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_failed"


async def test_empty_message_rejected(client, session_id):
    r = await client.post(f"/api/sessions/{session_id}/messages", json={"content": ""})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_failed"


async def test_messages_persist_and_order_by_seq(client, session_id):
    for text in ("first", "second", "third"):
        async with client.stream(
            "POST", f"/api/sessions/{session_id}/messages", json={"content": text}
        ) as r:
            assert r.status_code == 200
            async for _ in r.aiter_bytes():
                pass

    r = await client.get(f"/api/sessions/{session_id}/messages")
    assert r.status_code == 200
    msgs = r.json()
    assert [m["role"] for m in msgs] == [
        "user", "assistant", "user", "assistant", "user", "assistant"]
    assert [m["seq"] for m in msgs] == [1, 2, 3, 4, 5, 6]
    assert [m["content"] for m in msgs if m["role"] == "user"] == [
        "first", "second", "third"]
    # Provenance is stamped on assistant turns (UX contract requirement 7).
    for m in msgs:
        if m["role"] == "assistant":
            assert m["provider"] == "ollama"
            assert m["model"]
            assert m["latency_ms"] is not None


@pytest.mark.parametrize("n", [2])
async def test_sessions_are_isolated(client, n):
    """Adversarial: two sessions must never see each other's messages."""
    ids = []
    for i in range(n):
        r = await client.post("/api/sessions", json={"title": f"s{i}", "user_metadata": {}})
        ids.append(r.json()["id"])

    for i, sid in enumerate(ids):
        async with client.stream(
            "POST", f"/api/sessions/{sid}/messages",
            json={"content": f"secret-for-session-{i}"},
        ) as r:
            async for _ in r.aiter_bytes():
                pass

    for i, sid in enumerate(ids):
        msgs = (await client.get(f"/api/sessions/{sid}/messages")).json()
        blob = " ".join(m["content"] for m in msgs)
        assert f"secret-for-session-{i}" in blob
        for j in range(n):
            if j != i:
                assert f"secret-for-session-{j}" not in blob, (
                    f"session {i} leaked content from session {j}")
        # Every row belongs to this session. No exceptions.
        assert all(m["session_id"] == sid for m in msgs)
        # Sequence numbers restart per session — they are not global.
        assert [m["seq"] for m in msgs] == [1, 2]


async def test_delete_cascades_messages(client, session_id):
    async with client.stream(
        "POST", f"/api/sessions/{session_id}/messages", json={"content": "x"}
    ) as r:
        async for _ in r.aiter_bytes():
            pass

    assert (await client.delete(f"/api/sessions/{session_id}")).status_code == 204
    assert (await client.get(f"/api/sessions/{session_id}")).status_code == 404
    # Messages must be gone, not orphaned.
    assert (await client.get(f"/api/sessions/{session_id}/messages")).status_code == 404


async def test_posting_to_unknown_session_404s(client):
    r = await client.post(
        f"/api/sessions/{uuid.uuid4()}/messages", json={"content": "hi"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
