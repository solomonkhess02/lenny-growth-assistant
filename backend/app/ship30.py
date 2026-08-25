"""Ship 30 essay generation -- content transformation.

Skill 03 lists content transformation as its own concern, separate from
retrieval, model interaction and deterministic application logic. That is why
this is a module beside `agent.py` rather than another branch inside it: an
essay is a different product, built from an answer that already exists, and
mixing the two would put a 1,250-word writing task inside the code path that
has to stay a fast, tight question answerer.

Everything that makes an answer trustworthy is reused rather than reimplemented:

  - evidence comes from the database (rehydrated by chunk id, then topped up
    by the same deterministic search), never from the model;
  - `[E#]` labels are allocated by `agent.cite_label`, so a label means the
    same chunk in the essay as it did in the answer;
  - `grounding.verify_answer` runs on the finished essay, unconditionally;
  - generation goes through the provider seam, so the essay runs on the
    session's provider and nothing is ever substituted for it.

What is new here is only *how the writing instructions reach the model*.

## Why the skill is not delivered through `pi --skill`

Pi implements the Agent Skills standard with progressive disclosure: the
system prompt receives a skill's NAME and DESCRIPTION, and the body of
SKILL.md reaches the model only when the model calls the `read` tool
(pi-coding-agent `docs/skills.md`, "How Skills Work"). We run `--no-tools` --
a locked Phase 4 decision -- so `--skill` would inject a description the model
has no way to expand. Forcing it with `/skill:name` requires interactive mode,
which `-p` is not. Turning `read` back on to fix this would hand a web backend
a filesystem tool, which is exactly what `--no-tools` exists to prevent.

So the skill body is delivered through Pi's own prompt-file flags instead,
split by who owns the content:

    --system-prompt         app/prompts/ship30_rules.md   code-owned, non-negotiable
    --append-system-prompt  SKILL.md                      skill-owned, craft

Editing the skill changes how an essay reads. It cannot relax grounding,
because the rules the essay is checked against are not in it.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from .agent import cite_label, source_summaries
from .config import get_settings
from .errors import EvidenceUnavailable, ProviderMisconfigured
from .grounding import verify_answer
from .providers import ModelProvider, get_provider
from .retrieval import Evidence, evidence_by_chunk_ids, retrieve

log = logging.getLogger("app.ship30")

SKILL_NAME = "05-ship30-writing"

# Where the application-owned rules live, relative to this file.
_RULES_PATH = Path(__file__).parent / "prompts" / "ship30_rules.md"

# Skill file lookup order. The authored copy lives in `.claude/skills/` (host
# dev loop); the Dockerfile copies that same file to `app/skills/` because
# `.claude/` is in `.dockerignore` and would otherwise be absent from the
# runtime image -- a difference that shows up only in the graded deployment.
_SKILL_CANDIDATES = (
    Path(__file__).parent / "skills" / SKILL_NAME / "SKILL.md",
    Path(__file__).parents[2] / ".claude" / "skills" / SKILL_NAME / "SKILL.md",
)

# Markdown blockquote lines. Counted and reported, NOT treated as quotation:
# measured against the real Phase 1 essay, whose blockquotes are a sources
# list rather than pull-quotes, so extracting them produces false positives.
# A detector that flags honest work is as useless as one that misses lies.
_BLOCKQUOTE_RE = re.compile(r"^\s{0,3}>\s?\S", re.MULTILINE)

_H1_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*$", re.MULTILINE)


def word_count(text: str) -> int:
    """One definition of "a word", used for the reported and the stored count.

    Whitespace-split, matching how the Phase 1 measurements were taken, so the
    numbers in `spike/results/bench.json` and the numbers this product reports
    mean the same thing.
    """
    return len((text or "").split())


def title_of(markdown: str) -> str | None:
    """The essay's own H1, if it wrote one. Never invented on its behalf."""
    match = _H1_RE.search(markdown or "")
    return match.group(1).strip() if match else None


def blockquote_lines(markdown: str) -> int:
    """Blockquote lines present. Reported for visibility, never a verdict."""
    return len(_BLOCKQUOTE_RE.findall(markdown or ""))


def _read(path: Path, what: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProviderMisconfigured(
            f"Could not read the {what} at {path}: {exc.__class__.__name__}."
        ) from exc
    if not text.strip():
        raise ProviderMisconfigured(f"The {what} at {path} is empty.")
    return text


@lru_cache(maxsize=1)
def load_skill() -> tuple[str, str, str]:
    """Return (name, body, sha256) for the Ship 30 skill.

    The digest is provenance, not a checksum guard: an essay records which
    revision of the writing instructions produced it, so a change in house
    style is attributable later. Skill 03 asks agent execution to expose the
    selected skill; this is what makes that answerable rather than assumed.
    """
    configured = get_settings().ship30_skill_path.strip()
    candidates = (Path(configured),) if configured else _SKILL_CANDIDATES

    for path in candidates:
        if path.is_file():
            body = _read(path, "Ship 30 skill file")
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            log.info("ship30_skill_loaded", extra={
                "skill": SKILL_NAME, "path": str(path),
                "skill_sha256": digest, "chars": len(body)})
            return SKILL_NAME, body, digest

    looked = ", ".join(str(c) for c in candidates)
    raise ProviderMisconfigured(
        f"The Ship 30 skill file was not found (looked in: {looked}). "
        f"Set SHIP30_SKILL_PATH, or restore "
        f".claude/skills/{SKILL_NAME}/SKILL.md."
    )


@lru_cache(maxsize=1)
def load_rules() -> str:
    """The application-owned rules. Shipped with the code, never with content."""
    return _read(_RULES_PATH, "Ship 30 rules file")


def system_prompt() -> str:
    """What Pi receives as `--system-prompt`.

    Deliberately REPLACES Pi's default coding-assistant prompt rather than
    appending to it: this task has nothing to do with code, and replacing also
    overrides Pi's discovery of an ambient `~/.pi/agent/SYSTEM.md`, which could
    otherwise alter a grounded generation invisibly.
    """
    return load_rules()


@dataclass
class EssayEvidence:
    """The evidence an essay is written from, and where each piece came from.

    `carried` is the count of items rehydrated from the source answer. They
    occupy the first `carried` positions, which is what makes [E1]..[En] mean
    the same thing in the essay as in the answer the reader was looking at.
    """

    items: list[Evidence]
    carried: int

    @property
    def added(self) -> int:
        return len(self.items) - self.carried


async def assemble_evidence(
    db: AsyncSession,
    *,
    stored_sources: list[dict],
    question: str,
    k: int | None = None,
) -> EssayEvidence:
    """Pin the answer's evidence, then top it up from the same query.

    Two steps, in this order, and the order is the point:

      1. Rehydrate the exact chunks the answer cited, by id, in stored order.
         These keep their original labels. If any is gone the whole request
         fails -- see `evidence_by_chunk_ids`.
      2. Run the ORIGINAL question through the ordinary deterministic search
         at a larger k, drop anything already pinned, and append the rest.

    Step 2 is the existing `retrieve()` with a different `k`, which is already
    a parameter. The similarity floor and the per-source cap are untouched, so
    the pre-registered calibration (docs/retrieval-calibration.md) still holds:
    it measured where the FLOOR separates supported from unsupported questions,
    and that value has not moved.

    The bare question is used rather than `retrieve_for_session`, so the same
    answer yields the same essay evidence no matter how much conversation has
    happened since.
    """
    settings = get_settings()
    k = k or settings.essay_retrieval_k

    # Two different refusals, deliberately not collapsed into one. They have
    # different causes and different fixes, and a reader who is told the wrong
    # one goes looking in the wrong place.
    if not stored_sources:
        # No evidence at all: the answer was an abstention, so the model was
        # never invoked. Writing an essay anyway would mean writing it from the
        # model's own memory -- precisely what "no evidence, no answer" exists
        # to prevent. The route rejects this first; this is the backstop that
        # makes the rule hold for any caller of the module.
        raise EvidenceUnavailable(
            "That answer cited no evidence, so there is nothing to write an "
            "essay from."
        )

    chunk_ids = [s.get("chunk_id") for s in stored_sources]
    if not all(chunk_ids):
        # A card without a chunk id predates Phase 6. Refuse rather than write
        # from a partial reconstruction of what the reader was actually shown.
        raise EvidenceUnavailable(
            "That answer was recorded before essays could re-read their "
            "evidence, so it cannot be turned into one. Ask the question "
            "again and write the essay from the new answer."
        )

    carried = await evidence_by_chunk_ids(
        db, [str(c) for c in chunk_ids],
        similarities={str(s["chunk_id"]): float(s.get("similarity") or 0.0)
                      for s in stored_sources},
    )

    items = list(carried)
    if len(items) < k and question.strip():
        seen = {e.chunk_id for e in items}
        # The per-source cap is relaxed for the top-up (see
        # Settings.essay_max_per_source). The answer's cap is a diversity rule
        # for three chunks; an essay is allowed to go deeper into the episode
        # it is actually about. The similarity floor is unchanged, so nothing
        # below the calibrated threshold can enter either way.
        for extra in await retrieve(
                db, question, k,
                max_per_source=settings.essay_max_per_source):
            if extra.chunk_id in seen:
                continue
            items.append(extra)
            seen.add(extra.chunk_id)
            if len(items) >= k:
                break

    log.info("ship30_evidence_assembled", extra={
        "carried": len(carried), "added": len(items) - len(carried),
        "total": len(items), "k": k,
        "sources": sorted({e.source_id for e in items}),
    })
    return EssayEvidence(items=items, carried=len(carried))


def build_prompt(question: str, answer: str, evidence: list[Evidence]) -> str:
    """Render the user-side prompt: evidence, prior turn, task.

    The prior question and answer are included as framing -- they are what the
    reader asked for an essay ABOUT -- and are labelled as context that is not
    quotable. This is safe by construction rather than by instruction: the
    grounding haystack is built from the EVIDENCE only, so a model that quotes
    the previous answer verbatim gets flagged for fabrication. The failure mode
    leans in the conservative direction.
    """
    blocks = [
        f'[{cite_label(i)}] {e.speaker} on "{e.source_title}":\n{e.text}'
        for i, e in enumerate(evidence)
    ]
    tags = ", ".join(f"[{cite_label(i)}]" for i in range(len(evidence)))
    target = get_settings().essay_target_words
    joined = "\n\n".join(blocks)

    return (
        f"Evidence:\n\n{joined}\n\n"
        f"---\n\n"
        f"Context from the conversation (framing only -- NOT quotable, NOT "
        f"citable, and not a source of facts):\n\n"
        f"Question asked: {question}\n\n"
        f"Answer given: {answer}\n\n"
        f"---\n\n"
        f"TASK: Write a Ship 30 for 30-style essay of approximately {target} "
        f"words on what the evidence above says about this topic.\n\n"
        f"Draw every substantive claim from the evidence and cite it with "
        f"{tags}. Follow the structure and style in your instructions. "
        f"Return Markdown only, starting with the essay's title."
    )


async def stream_essay(
    db: AsyncSession,
    *,
    question: str,
    answer: str,
    stored_sources: list[dict],
    provider: ModelProvider | None = None,
    k: int | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    """Yield (event_name, payload) pairs, same protocol as `agent.stream_answer`.

    Events: sources -> delta* -> grounding -> complete

    Reusing the answer protocol exactly is what lets the essay inherit the
    locked Provider UX contract without inventing a second one: streaming is
    baseline UX for every model request, citations precede text because they
    are evidence rather than claims, and verification necessarily follows the
    text it verifies -- which is why a failed verdict retracts an essay the
    same way it retracts an answer.
    """
    settings = get_settings()
    provider = provider or get_provider()
    t0 = time.perf_counter()

    skill_name, skill_body, skill_sha = load_skill()
    assembled = await assemble_evidence(
        db, stored_sources=stored_sources, question=question, k=k)
    evidence = assembled.items

    yield "sources", {
        "count": len(evidence),
        "supported": bool(evidence),
        "carried": assembled.carried,
        "added": assembled.added,
        "sources": source_summaries(evidence),
    }

    parts: list[str] = []
    # See agent.stream_answer for why `aclosing` is required here: without
    # it, a disconnect mid-essay left the provider (and, on a cloud provider,
    # token spend) running as an orphan until the event loop's async-
    # generator finalizer happened to collect this frame.
    async with aclosing(provider.stream(
            build_prompt(question, answer, evidence),
            system_prompt=system_prompt(),
            append_system_prompt=skill_body)) as stream:
        async for delta in stream:
            parts.append(delta)
            yield "delta", {"text": delta}

    markdown = "".join(parts).strip()
    report = verify_answer(markdown, evidence)          # MANDATORY
    latency = int((time.perf_counter() - t0) * 1000)

    words = word_count(markdown)
    target = settings.essay_target_words
    tolerance = settings.essay_word_tolerance
    low, high = int(target * (1 - tolerance)), int(target * (1 + tolerance))
    within = low <= words <= high

    log.info("ship30_essay_generated", extra={
        "provider": provider.name, "model": provider.model,
        "skill": skill_name, "skill_sha256": skill_sha,
        "evidence_count": len(evidence), "carried": assembled.carried,
        "word_count": words, "within_target": within,
        "blockquote_lines": blockquote_lines(markdown),
        "grounding_verdict": report.verdict,
        "fabricated_quotes": len(report.fabricated_quotes),
        "invalid_tags": report.invalid_tags,
        "duration_ms": latency,
        "outcome": "ok" if report.grounded else "ungrounded"})

    yield "grounding", report.to_dict()
    yield "complete", {
        "markdown": markdown,
        "title": title_of(markdown),
        # Reported, never enforced: truncating to hit a number would cut quotes
        # and citation tags mid-sentence and could turn verified prose into a
        # fabrication. An honest number beats a corrected one.
        "word_count": words,
        "target_words": target,
        "within_target": within,
        "blockquote_lines": blockquote_lines(markdown),
        "trustworthy": report.grounded,
        "supported": bool(evidence),
        "provider": provider.name,
        "model": provider.model,
        "skill": skill_name,
        "skill_sha256": skill_sha,
        "latency_ms": latency,
    }
