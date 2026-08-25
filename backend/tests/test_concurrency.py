"""Concurrency regression tests for sequence allocation (H1).

Before the repository layer, `seq` was allocated as MAX(seq)+1 in one
module and inserted in another, with no lock. Measured against the running
stack: 6 concurrent posts to ONE session produced 2 successes and 4
failures, and the failures were reported as HTTP 503
"database_unavailable" while the database was perfectly healthy.

These tests pin both halves of that defect.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.test_streaming import _stream


async def _new_session(client) -> str:
    r = await client.post("/api/sessions", json={"title": "c", "user_metadata": {}})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_six_concurrent_posts_to_one_session_all_succeed(client):
    """The exact scenario that previously failed 4 of 6."""
    sid = await _new_session(client)
    n = 6

    results = await asyncio.gather(*[
        client.post(f"/api/sessions/{sid}/messages",
                    json={"content": f"concurrent-{i}"})
        for i in range(n)
    ], return_exceptions=True)

    failures = [r for r in results
                if isinstance(r, BaseException) or r.status_code != 200]
    assert not failures, (
        f"{len(failures)}/{n} concurrent posts failed: "
        f"{[getattr(f, 'text', repr(f)) for f in failures]}"
    )

    msgs = (await client.get(f"/api/sessions/{sid}/messages")).json()
    seqs = [m["seq"] for m in msgs]

    # 6 user turns + 6 assistant turns, each with a distinct seq.
    assert len(msgs) == 2 * n, f"expected {2 * n} messages, got {len(msgs)}"
    assert len(seqs) == len(set(seqs)), f"duplicate seq allocated: {seqs}"
    assert seqs == list(range(1, 2 * n + 1)), f"seqs not contiguous: {seqs}"

    # Every user turn survived; none were lost to a lost-update race.
    sent = {f"concurrent-{i}" for i in range(n)}
    assert {m["content"] for m in msgs if m["role"] == "user"} == sent


async def test_concurrent_posts_across_sessions_do_not_block_each_other(client):
    """The lock is per-session: different sessions stay parallel.

    Guards against 'fixing' the race with a global lock, which would
    serialise every conversation in the deployment.
    """
    sids = [await _new_session(client) for _ in range(4)]

    results = await asyncio.gather(*[
        client.post(f"/api/sessions/{sid}/messages", json={"content": "x"})
        for sid in sids
    ])
    assert all(r.status_code == 200 for r in results)

    for sid in sids:
        msgs = (await client.get(f"/api/sessions/{sid}/messages")).json()
        # Each session numbers from 1 independently.
        assert [m["seq"] for m in msgs] == [1, 2]


async def test_concurrent_posts_keep_sessions_isolated(client):
    """Isolation must survive concurrency, not just sequential use."""
    a, b = await _new_session(client), await _new_session(client)

    await asyncio.gather(*[
        client.post(f"/api/sessions/{a}/messages", json={"content": f"alpha-{i}"})
        for i in range(3)
    ], *[
        client.post(f"/api/sessions/{b}/messages", json={"content": f"beta-{i}"})
        for i in range(3)
    ])

    a_text = " ".join(m["content"] for m in
                      (await client.get(f"/api/sessions/{a}/messages")).json())
    b_text = " ".join(m["content"] for m in
                      (await client.get(f"/api/sessions/{b}/messages")).json())

    assert "beta-" not in a_text
    assert "alpha-" not in b_text


async def test_integrity_error_maps_to_conflict_not_database_unavailable():
    """Drive the dependency's error mapping directly.

    Behavioural, not textual: an earlier version of this test asserted the
    ORDER OF SOURCE LINES via inspect.getsource, which passes whether or not
    the mapping actually works and breaks on harmless reformatting.
    """
    from sqlalchemy.exc import IntegrityError

    from app.db import get_session
    from app.errors import ResourceConflict

    gen = get_session()
    await gen.__anext__()
    with pytest.raises(ResourceConflict) as e:
        await gen.athrow(IntegrityError("INSERT ...", {}, Exception("duplicate key")))

    assert e.value.code == "conflict"
    assert e.value.http_status == 409
    assert e.value.retryable is True
    # The whole point: a constraint violation is not an outage.
    assert "not reachable" not in e.value.message


async def test_connection_failure_still_maps_to_database_unavailable():
    """The counterpart: a real outage must NOT be downgraded to a conflict."""
    from app.db import get_session
    from app.errors import DatabaseUnavailable

    gen = get_session()
    await gen.__anext__()
    with pytest.raises(DatabaseUnavailable) as e:
        await gen.athrow(OSError("connection refused"))

    assert e.value.code == "database_unavailable"
    assert e.value.http_status == 503


async def test_db_errors_maps_integrity_error_to_conflict():
    """D2 (Phase 8). `db_errors()` -- the streaming-path counterpart of
    `get_session` -- must apply the identical mapping.
    """
    from sqlalchemy.exc import IntegrityError

    from app.db import db_errors
    from app.errors import ResourceConflict

    with pytest.raises(ResourceConflict) as e:
        async with db_errors():
            raise IntegrityError("INSERT ...", {}, Exception("duplicate key"))

    assert e.value.code == "conflict"
    assert e.value.http_status == 409
    assert e.value.retryable is True


async def test_db_errors_maps_connection_failure_to_database_unavailable():
    """The counterpart: a real outage must NOT be downgraded to a conflict."""
    from app.db import db_errors
    from app.errors import DatabaseUnavailable

    with pytest.raises(DatabaseUnavailable) as e:
        async with db_errors():
            raise OSError("connection refused")

    assert e.value.code == "database_unavailable"
    assert e.value.http_status == 503
    assert e.value.retryable is True


async def test_db_errors_does_not_touch_unrelated_exceptions():
    """An AppError or any other failure must pass through unchanged -- this
    context manager maps DB-shaped failures only, never anything else."""
    from app.db import db_errors
    from app.errors import ProviderUnavailable

    with pytest.raises(ProviderUnavailable):
        async with db_errors():
            raise ProviderUnavailable("dead provider")


async def test_mid_stream_assistant_write_failure_reports_database_unavailable(
        client, session_id, monkeypatch):
    """D2 (Phase 8). The assistant-turn write happens on a hand-built session
    (`session_factory()`), not `Depends(get_session)`, because the generator
    keeps running after the endpoint has returned. Before `db_errors()` wrapped
    it, a DB failure there fell through to a generic `internal_error`,
    non-retryable -- the exact mismapping the two tests above exist to catch
    at the unit level, reproduced here end-to-end through the real stream.
    """
    from app import repository as repo

    real_append = repo.append_message

    async def flaky_append(db, session_id, **kwargs):
        if kwargs.get("role") == "assistant":
            raise OSError("connection refused")
        return await real_append(db, session_id, **kwargs)

    monkeypatch.setattr(repo, "append_message", flaky_append)

    events = await _stream(client, session_id, "trigger the failure")
    kinds = [e for e, _ in events]

    assert kinds[-1] == "error", f"stream did not end in an error: {kinds}"
    err = events[-1][1]
    assert err["code"] == "database_unavailable"
    assert err["retryable"] is True
    assert "done" not in kinds


async def test_append_to_deleted_session_raises_not_found(client):
    """The existence check happens under the same lock as the insert."""
    sid = await _new_session(client)
    assert (await client.delete(f"/api/sessions/{sid}")).status_code == 204

    r = await client.post(f"/api/sessions/{sid}/messages", json={"content": "x"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
