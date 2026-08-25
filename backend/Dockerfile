# ---------- stage 1: build the frontend ----------
FROM node:22-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- stage 2: install the Pi agent framework ----------
# Pi is the §3.1 agent framework (docs/agent-framework-comparison.md). It is an
# npm package, so it needs a Node runtime -- but this is a Python image, and
# `apt-get install nodejs npm` would pull Debian's older Node plus a second
# toolchain into the runtime. Instead Pi is installed under its own prefix here
# and only that prefix, plus the single Node binary, is copied forward.
FROM node:22-slim AS piagent
RUN npm install -g --prefix /opt/pi --no-audit --no-fund \
        @earendil-works/pi-coding-agent

# ---------- stage 3: runtime ----------
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# The Node binary is dynamically linked against libstdc++, which python:slim
# does not ship. Without this, `node --version` fails and every generation
# request dies at subprocess start.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=piagent /opt/pi /opt/pi
ENV PATH="/opt/pi/bin:${PATH}"

WORKDIR /srv

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/migrations ./migrations

# The Ship 30 skill, from its single authored location. Copied rather than
# duplicated in the repo so there is exactly one copy to keep correct; see
# app/ship30.py for the lookup order (app/skills first, .claude/skills second).
COPY .claude/skills/05-ship30-writing/SKILL.md ./app/skills/05-ship30-writing/SKILL.md
COPY backend/alembic.ini ./

# Serving the built SPA from FastAPI keeps the stack to ONE app container
# rather than adding a Node runtime just to hand over static files.
COPY --from=frontend /fe/dist ./app/static

# Non-root: the container has no reason to run privileged.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /srv

# Pi provider config. Credential-free by construction -- see deploy/pi-models.json.
# DeepSeek is NOT listed there: it is a built-in Pi provider whose key is
# resolved from DEEPSEEK_API_KEY at runtime.
COPY --chown=appuser:appuser deploy/pi-models.json /home/appuser/.pi/agent/models.json

# Pi's working directory. Deliberately NOT /srv: Pi discovers and injects
# project context files (CLAUDE.md, AGENTS.md) from its cwd, and doing that
# from the application root would silently prepend them to every prompt.
ENV PI_WORKING_DIR=/tmp/pi-workdir
RUN mkdir -p /tmp/pi-workdir && chown appuser:appuser /tmp/pi-workdir

USER appuser

# Pi resolves its config relative to HOME; make it explicit rather than relying
# on the shell to set it.
ENV HOME=/home/appuser

EXPOSE 8000

# Migrations run at start so `docker compose up` is genuinely one command.
# `alembic upgrade head` is idempotent, so a restart is safe.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]

# ---------- stage 4: test image ----------
# Extends the REAL runtime rather than rebuilding, so container tests exercise
# the same layers that ship. Kept as a separate stage so pytest and the test
# suite never enter the production image.
FROM runtime AS test
USER root
COPY backend/requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY --chown=appuser:appuser backend/tests ./tests
COPY backend/pytest.ini ./

# Test fixtures resolve as parents[2] from /srv/tests, i.e. the filesystem
# root. Only the two directories the suite actually reads are copied:
#   spike/evidence  - real transcripts for the offline parse/chunk tests
#   spike/results   - real Phase 1 model output for the grounding regressions
#   data/transcripts- the ingested corpus, for citation-resolution tests
COPY --chown=appuser:appuser spike/evidence /spike/evidence
COPY --chown=appuser:appuser spike/results /spike/results
COPY --chown=appuser:appuser data/transcripts /data/transcripts

USER appuser
CMD ["pytest", "-q"]
