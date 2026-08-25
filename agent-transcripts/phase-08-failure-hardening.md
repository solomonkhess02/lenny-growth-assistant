# Phase 8 — Failure-mode hardening

**Commit:** `af8af64 feat(phase-8): failure-mode hardening — timeouts, teardown, taxonomy, unverified output`

The phase brief was "handle failure modes." An audit of the actual code paths found something more
specific: three of the four problems were not *missing* features but *wrong behaviour* — code that
ran, produced a result, and produced the wrong one.

---

## D3 — partial output read as a verified answer

**The most serious defect found in the whole build.**

### The path

When a provider dies mid-stream, after deltas have already been sent:

1. `verify_answer` never runs — it is unreachable past the exception.
2. So `grounding` is `null`.
3. `Message.tsx` applied its retraction styling only when `state === "retracted"`.
4. `state` is `error`, not `retracted`.
5. The verdict banner renders only when `grounding` is set — so **nothing rendered at all.**

The result: several hundred words of unverified model output, in a normal body div, with no
strike-through, no dimming, no banner and no verdict — **visually identical to an answer that had
passed verification.**

The Phase 5 retraction property had a hole in it, and the hole was in the one case where the reader
has the least reason to suspect anything.

### Why the audit found it and the tests did not

Every existing test asserted on what the stream *emits*. The backend was correct throughout — it
emitted no fabricated verdict, which is exactly right. The defect was entirely in what the absence
of a verdict *looked like*, and no test had an opinion about that.

### The correction

`interrupted` and `untrustworthy` derived in `Message.tsx` and `ArtifactPane.tsx`: any turn ending
in `error` after at least one delta gets `.retracted-text` plus a distinct `.unverified-note` —
worded differently from a retraction, because it is a different claim ("never checked" rather than
"checked and failed").

Verified in a real browser against a genuinely interrupted essay: 387 real words at computed
`opacity: 0.55` and `text-decoration-line: line-through`, the banner visibly rendered at 329×80 px,
and **`verdictBadgeCount: 0`** — no verdict, pass or fail, invented for text that was never
checked.

---

## D2 — a healthy database reported as an outage, again

Phase 2 split `ResourceConflict` (409) from `DatabaseUnavailable` (503) precisely so a constraint
conflict would not be reported as an outage.

The streaming routes bypassed that mapping entirely — and for a legitimate reason: they build
sessions from `session_factory()` directly, because a request-scoped `Depends` cannot outlive the
returned response. Going around `get_session` meant going around its error ladder.

So a DB failure during the assistant write surfaced as `internal_error` / **500** /
`retryable: false`, where the identical failure on any other route is `database_unavailable` /
**503** / `retryable: true`. Same fault, opposite advice to the client, depending only on which
route hit it.

**The correction:** `db.db_errors()`, an async context manager reusing the *exact same* ladder
without creating a session. One mapping, two consumers — rather than a second copy that could drift
from the first.

---

## D1 — abandoned generations cleaned up by the garbage collector

Disconnect *detection* existed and logged. What did not exist was teardown.

The `return` closed nothing. Cleanup depended on the async-generator finalizer hook eventually
collecting a three-link chain: router generator → `stream_answer` (which had no `try/finally` at
all) → `PiRuntime.stream`'s `finally`.

Between the reader leaving and the collector running, the provider kept generating — burning cloud
tokens for output nobody would see, with **no bound on when, or whether, that stopped.**

**The correction:** `aclosing(...)` around the provider stream in `stream_answer` and
`stream_essay`, making teardown deterministic. On POSIX the child is spawned into its own process
group and killed as a group, because Pi is a CLI shim over Node and killing the direct child left
grandchildren alive. `CancelledError` is caught, logged, and **re-raised** — never swallowed.

Verified against real Ollama in the container: an abandoned generation's process was dead in
`/proc` immediately (`aclose()` returned in 0.00 s), rather than eventually.

---

## Generation timeout — and a trap in the obvious implementation

There was no timeout at all. `readline()` blocks forever, and the httpx read timeout does not apply
because generation runs through a subprocess, not the HTTP client.

The obvious implementation is wrong in an interesting way:

```python
async with asyncio.timeout(limit):     # DON'T
    ...
    yield payload
```

That block contains a `yield`. An async generator suspended at a `yield` is suspended in the
**consumer's** task — so the deadline keeps running while the router does database writes, and
cancellation can fire while control is outside the generator entirely.

**The correction:** enforce the deadline per `await` on the `readline()`, with a `perf_counter()`
comparison for the total. An idle bound is primary (a provider that has gone quiet) and a total
bound is the backstop (a provider emitting slowly forever), so a slow-but-alive local model never
trips it — which is its own regression test.

---

## Four self-inflicted mistakes while building this

Worth recording, because they are the ordinary texture of the work rather than the highlights.

**1. Monkeypatching `os.name` broke `pathlib`.** To test the POSIX process-group branch on a
Windows host, the agent set `os.name = "posix"` globally. `pathlib` reads `os.name` to decide which
`Path` class to build, so unrelated code raised
`UnsupportedOperation: cannot instantiate 'PosixPath' on your system`.
*Fixed by* extracting a `_use_process_group()` function as the patch seam — a narrow, named
decision point instead of a global.

**2. `signal.SIGKILL` does not exist on Windows.** The test then failed with `AttributeError`.
*Fixed with* `@pytest.mark.skipif(not hasattr(signal, "SIGKILL"))` — it skips loudly on the host
and **runs and passes in the Linux container**, which is what makes the skip acceptable rather than
a gap.

**3. A comment broke a test.** A new explanatory comment used the word "subprocess" in `agent.py`,
tripping a boundary-discipline test that asserts no Pi-specific vocabulary leaks into the
orchestration layer. The test was right and the comment was reworded. An architectural rule that is
enforced mechanically will occasionally catch prose — that is the rule working.

**4. Two pytest runs raced on one database.** The host suite and the container suite were started
concurrently, and both point at the same physical Postgres. Both call `drop_all()`/`create_all()`,
so tables vanished underneath the other run: `relation "sessions" does not exist`.
*Fixed by* not doing that — but it is a real hazard of a shared test fixture and is worth knowing
before it looks like a flaky test.

---

## One environment trap that nearly produced a false result

While verifying the D3 fix in a browser, `127.0.0.1:8000` and `localhost:8000` returned **different
applications**. A stale, unrelated process was bound to the IPv4 address, serving a build with no
Phase 8 settings at all; `localhost` (via `::1`) reached the actual container.

Caught by comparing `/api/config` from both, after the first results looked wrong. Had it not been
noticed, the verification would have been run against the wrong binary and reported as a failure of
code that was in fact correct — or worse, as a pass of code that had never been loaded.

The same lesson as Phase 2's `localhost`/`127.0.0.1` finding, in a different costume: **on this
platform those two names are not interchangeable, and assuming they are wastes hours.**
