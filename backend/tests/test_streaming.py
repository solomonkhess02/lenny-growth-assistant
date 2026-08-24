"""Streaming transport.

Requirement 1 of the locked Provider UX contract: streaming is baseline UX for
every model request. These tests prove the transport delivers *incrementally*
rather than buffering and flushing once — a distinction a naive test misses.
"""
from __future__ import annotations

import json


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


async def test_stream_emits_meta_then_deltas_then_done(client, session_id):
    async with client.stream(
        "POST", f"/api/sessions/{session_id}/messages",
        json={"content": "hello world"},
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        raw = "".join([chunk async for chunk in r.aiter_text()])

    events = parse_sse(raw)
    kinds = [e for e, _ in events]

    assert kinds[0] == "meta", "meta must arrive first so the UI can label the turn"
    assert kinds[-1] == "done"
    assert "delta" in kinds

    meta = events[0][1]
    assert meta["provider"] == "ollama"
    assert meta["model"]
    assert meta["session_id"] == session_id

    text = "".join(p["text"] for e, p in events if e == "delta")
    assert "hello world" in text

    done = events[-1][1]
    assert done["seq"] == 2
    assert done["latency_ms"] >= 0
    assert done["content_length"] == len(text.strip())


async def test_stream_is_incremental_not_one_blob(client, session_id):
    """More than one delta frame, else it is not really streaming."""
    async with client.stream(
        "POST", f"/api/sessions/{session_id}/messages",
        json={"content": "one two three four five six"},
    ) as r:
        raw = "".join([c async for c in r.aiter_text()])
    deltas = [e for e, _ in parse_sse(raw) if e == "delta"]
    assert len(deltas) > 5, f"expected many delta frames, got {len(deltas)}"


async def test_streamed_assistant_turn_is_persisted(client, session_id):
    async with client.stream(
        "POST", f"/api/sessions/{session_id}/messages", json={"content": "persist me"}
    ) as r:
        raw = "".join([c async for c in r.aiter_text()])
    done = [p for e, p in parse_sse(raw) if e == "done"][0]

    msgs = (await client.get(f"/api/sessions/{session_id}/messages")).json()
    assistant = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["id"] == done["message_id"]
    assert assistant[0]["content"]
    # The user turn is persisted even though the stream came after it.
    assert [m["content"] for m in msgs if m["role"] == "user"] == ["persist me"]
