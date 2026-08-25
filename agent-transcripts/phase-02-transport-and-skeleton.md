# Phase 2 — Transport and application skeleton

**Commits:** `8cb9e2b feat(phase-2a): lock OD-1/OD-2 and the Ollama transport by measurement` ·
`0f059e1 feat(phase-2b): FastAPI + Postgres skeleton with streaming and provider seam` ·
`5e35d79 fix(H1+M2): atomic sequence allocation behind a persistence layer`

---

## Correction 1 — `localhost` is not `127.0.0.1`

### What the agent wrote

```python
OLLAMA_BASE_URL = "http://localhost:11434"
```

Which is what everyone writes, including Ollama's own documentation.

### The symptom

Every request had a consistent, unexplained delay of roughly two seconds. Not a timeout, not an
error — just slow, uniformly, on every new connection. Easy to dismiss as "local model inference
is slow."

### What it actually was

`localhost` resolves to `::1` (IPv6) first on this system. Ollama binds IPv4 only. Every new
connection attempted IPv6, waited for it to fail, and fell back.

Measured at the raw TCP level, removing the model from the picture entirely:

| Host | Time to connect |
|---|---|
| `localhost` | **2032 ms** |
| `127.0.0.1` | **0.53 ms** |

A factor of ~3,800, and nothing about it looks like a bug from inside the application.

### The correction

`127.0.0.1` everywhere, with the measurement written into
[`.env.example`](../.env.example) at the variable itself, so the next person to "clean up" the
config finds out why before changing it.

---

## Correction 2 — a race in sequence allocation, caught before it shipped

### What the agent wrote

Message ordering within a session uses a monotonic `seq` column. The first implementation read the
current maximum and wrote max + 1:

```python
seq = (max existing seq for this session) + 1     # then INSERT
```

This passes every single-threaded test.

### What was wrong

Two concurrent posts to the same session read the same maximum and write the same `seq`. Since
sessions are the unit of concurrency in a chat product, this is not an exotic case — it is two
browser tabs.

It was found during the phase's own self-review, not by a failing test, because no test yet
exercised concurrency.

### The correction

The whole commit `5e35d79` exists for this. Two parts:

1. **A persistence layer.** Sequence allocation moved into `repository.append_message` and became
   atomic, so no caller can get it wrong. There is one place to be correct.
2. **A unique index** — `ix_messages_session_seq (session_id, seq)`, unique — so that if the
   allocation is ever wrong again, the database refuses the write rather than silently accepting
   two messages with the same position.

Then tests that would have caught it: [`tests/test_concurrency.py`](../backend/tests/test_concurrency.py).

### The follow-on defect this exposed

Adding the unique index created a *second* problem, and the fix for it is one of the more
instructive moments in the build.

An `IntegrityError` from that index was being caught by the same handler as connection failures.
Both are `SQLAlchemyError` subclasses, so a constraint conflict — a perfectly healthy database
correctly rejecting a duplicate — was reported to the client as **`database_unavailable`, 503: the
database is down.**

An operator paged by that would go looking for an outage that did not exist.

The correction split them: `ResourceConflict` (409, retryable) is distinct from
`DatabaseUnavailable` (503, retryable), asserted by
`test_integrity_error_maps_to_conflict_not_database_unavailable`. That distinction became a
standing rule, and Phase 8 had to reinstate it on the streaming path where it had been bypassed
(see [Phase 8](phase-08-failure-hardening.md)).

---

## What these two have in common

Neither was found by reading code. The first needed a stopwatch on a TCP connection; the second
needed someone to ask "what happens if this runs twice at once?" and then write the index that
makes the answer unrepresentable.

Both fixes moved correctness from *convention* to *construction* — a config value with its
measurement attached, and a database constraint that cannot be forgotten. That pattern is
deliberate and recurs throughout: where a rule can be made structural, it is.
