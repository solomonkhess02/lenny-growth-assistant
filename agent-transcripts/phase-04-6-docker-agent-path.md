# Phase 4.6 — The Docker agent path

Getting Pi into the shipped image, and two defects that exist **only** in the container — which is
exactly the environment the evaluator uses.

---

## Correction 1 — the API served pytest

### The symptom

`docker compose up`, then nothing. The API container started, produced test-runner output, and
exited. No server.

### The cause

```yaml
build:
  context: .
  dockerfile: backend/Dockerfile
  # target: not specified
```

An unpinned build resolves to the **last stage** in the Dockerfile. The last stage is `test`, whose
`CMD` is `pytest -q`.

So the deployment shipped the test runner as the application entrypoint. The Dockerfile was
correct; the Compose file simply did not say which of its four stages was the product.

### The correction

```yaml
target: runtime      # MUST be pinned
```

With a comment in [`docker-compose.yml`](../docker-compose.yml) explaining what happens without it,
because the failure is total and the cause is an absent line rather than a wrong one — nothing to
notice while reading.

**Why the stage order was not simply changed:** the `test` stage extends `runtime` deliberately, so
container tests exercise the same Pi install, the same Node binary and the same provider config
that actually ship. Reordering to make the default safe would have broken that.

---

## Correction 2 — the container test count was two phases stale

### The symptom

"251 passed in the container" was recorded at Phase 4.6. At Phase 5 it was still 251. At Phase 6,
still 251 — while the host suite had grown by dozens of tests.

A frozen number across three phases is not stability. It is a measurement that stopped measuring.

### The cause

```bash
docker compose --profile test run --rm api-tests    # does NOT rebuild
```

`run` uses the existing image. Every "container run" for two phases had been executing a cached
pre-Phase-5 image — testing code that no longer existed, and reporting green.

### The correction

Build first, always:

```bash
docker compose --profile test build api-tests
docker compose --profile test run --rm api-tests
```

Written into [`CLAUDE.md`](../CLAUDE.md) and the [README](../README.md), and the standing rule
became: **check the collected count, not just the colour.** A count that does not move when the
suite grows is itself the alarm.

This pairs with the skip rule from the same period — `corpus_ready`, `ollama_ready` and `pi_ready`
skip *loudly*, and a green run full of skips is not a pass. Both exist because "the tests passed"
had twice turned out to mean something other than what it sounded like.

---

## Correction 3 — Postgres 18 refused to start

### The symptom

```
there appears to be PostgreSQL data in /var/lib/postgresql/data (unused mount/volume)
```

A hard error on startup, from the mount path that is correct for every Postgres image before 18.

### The cause

Postgres 18 stores data in a **major-version subdirectory**. Mounting a volume at the old
`/var/lib/postgresql/data` path now conflicts with it rather than matching it.

### The correction

```yaml
volumes:
  - lenny_pgdata:/var/lib/postgresql      # NOT /var/lib/postgresql/data
```

A **named volume, never a Windows bind mount** — bind-mounting `PGDATA` on Windows produces
permission and fsync problems that surface much later and much more confusingly than a startup
error does.

---

## Correction 4 — a negation in `.dockerignore` holds essay generation together

`.dockerignore` excludes `.claude/` — sensible, it is agent configuration, not application code.

But `.claude/skills/05-ship30-writing/SKILL.md` is **live prompt input**: `ship30.py` passes it to
Pi as `--append-system-prompt` and stamps its sha256 on every essay.

So the file needs a re-inclusion:

```
.claude/                                     # excluded
!.claude/skills/05-ship30-writing/           # except this
```

Delete that second line and the `COPY` in the Dockerfile fails — breaking essay generation **only
in the container**, which is the demo path.

It is documented in [`CLAUDE.md`](../CLAUDE.md) as load-bearing, and there are packaging tests that
read `Dockerfile` and `.dockerignore` to assert the pairing. Those tests are among the 6 that skip
inside the image, loudly, because the image deliberately does not contain those files — a skip with
a stated reason rather than a silent pass.

---

## The pattern across all four

Each of these is invisible on the host. The application ran perfectly under `uvicorn` throughout.

Docker is not a packaging step here; it is a **different environment with its own failure modes**,
and it is the one the evaluator will use. That is why the container test suite exists at all, and
why Phase 8's real-Ollama verification was run inside the container rather than on the host.
