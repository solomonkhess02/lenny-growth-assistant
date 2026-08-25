"""Ship 30 essay generation -- the module, not the transport.

What is actually being protected here:

  - the writing instructions reach the model as data we control, so an essay
    cannot be produced without the grounding rules attached;
  - evidence is carried over from the answer the reader was looking at, with
    its labels intact, and is never silently replaced by a fresh search;
  - the finished essay is verified like any other generated text;
  - the word target is measured and reported, never enforced by cutting.

A stub provider is used for the same reason `test_agent.py` uses one: these
are tests about what happens when a model misbehaves, and a real model cannot
be asked to fabricate on cue. It also keeps a 10-minute local generation out of
the default suite.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app import ship30
from app.errors import EvidenceUnavailable, ProviderMisconfigured
from app.providers import ModelProvider
from app.retrieval import Evidence

pytestmark = pytest.mark.usefixtures("corpus_ready", "ollama_ready")

QUESTION = "How does Duolingo use streaks to improve retention?"
EVIDENCE_TEXT = "Streaks work because loss aversion is powerful."


class StubProvider(ModelProvider):
    """Records exactly what it was asked to write with."""

    name = "stub"

    def __init__(self, text: str = "# Essay\n\nA claim. [E1]") -> None:
        self.text = text
        self.calls = 0
        self.last_prompt: str | None = None
        self.last_system: str | None = None
        self.last_append: str | None = None

    @property
    def pi_provider(self) -> str:
        return "stub-pi-provider"

    @property
    def model(self) -> str:
        return "stub-model"

    @property
    def base_url(self) -> str:
        return "stub://"

    async def check(self) -> dict:
        return {"provider": self.name, "reachable": True}

    async def stream(self, prompt: str, *, system_prompt: str | None = None,
                     append_system_prompt: str | None = None) -> AsyncIterator[str]:
        self.calls += 1
        self.last_prompt = prompt
        self.last_system = system_prompt
        self.last_append = append_system_prompt
        for word in self.text.split(" "):
            yield word + " "


def _evidence(n: int = 3, text: str = EVIDENCE_TEXT) -> list[Evidence]:
    return [
        Evidence(
            source_id=f"ep-{i}", source_title=f"Episode {i}",
            speaker="Jackson Shuttleworth", source_url="https://youtu.be/x",
            transcript_id=f"t{i}", chunk_id=f"c{i}", publish_date=None,
            chunk_index=i, guest="Jackson Shuttleworth", text=text,
            start_seconds=10 * i, end_seconds=10 * i + 5, similarity=0.7,
            citation_url=f"https://youtu.be/x?t={10 * i}",
        )
        for i in range(n)
    ]


@pytest.fixture(scope="module")
async def stored(corpus_engine) -> list[dict]:
    """Citation cards exactly as a real answer would have persisted them.

    Retrieved from the real corpus rather than hand-built, so the chunk ids are
    ids that actually resolve -- which is the whole mechanism under test.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.agent import source_summaries
    from app.retrieval import retrieve

    async with AsyncSession(corpus_engine) as db:
        return source_summaries(await retrieve(db, QUESTION, 2))


async def _run(db, provider, **kw) -> dict:
    """Drive stream_essay and return {event: payload} with deltas joined."""
    out: dict = {"delta": ""}
    async for event, payload in ship30.stream_essay(db, provider=provider, **kw):
        if event == "delta":
            out["delta"] += payload["text"]
        else:
            out[event] = payload
    return out


# --------------------------------------------------------------------------
# The skill reaches the model as data we control
# --------------------------------------------------------------------------
def test_skill_loads_and_is_fingerprinted():
    name, body, digest = ship30.load_skill()
    assert name == "05-ship30-writing"
    # The real content, not a placeholder.
    assert "Ship 30" in body and "Hook" in body
    assert len(digest) == 64, "sha256 provenance is missing"


def test_skill_digest_tracks_the_file_content():
    """The digest provenances a revision, so it must follow the bytes."""
    import hashlib
    _, body, digest = ship30.load_skill()
    assert digest == hashlib.sha256(body.encode("utf-8")).hexdigest()


def test_rules_are_application_owned_not_skill_owned():
    """The grounding contract must not live in editable skill content.

    This is the whole point of the split: someone rewriting the house style
    cannot switch off verbatim quoting or citation validity by doing it.
    """
    rules = ship30.load_rules()
    _, skill_body, _ = ship30.load_skill()

    for rule in ("ONLY the provided evidence", "VERBATIM", "[E1]"):
        assert rule in rules, f"rules file lost a non-negotiable: {rule}"
        assert rule not in skill_body, (
            f"{rule!r} lives in the skill file, where an edit could remove it")


def test_missing_skill_file_fails_loudly(monkeypatch):
    """S5. Pi must never be spawned with a path-as-prompt.

    Pi treats a non-existent --system-prompt path as literal prompt TEXT, so a
    wrong path silently becomes the instructions. The application has to catch
    that itself; nothing downstream can.
    """
    ship30.load_skill.cache_clear()
    monkeypatch.setattr(ship30, "_SKILL_CANDIDATES", ())
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "ship30_skill_path", "", raising=False)

    with pytest.raises(ProviderMisconfigured) as exc:
        ship30.load_skill()
    assert "not found" in str(exc.value)
    ship30.load_skill.cache_clear()


async def test_generation_carries_rules_and_skill_separately(corpus_db, stored):
    """S1. Both halves arrive, in the two channels they belong to."""
    stub = StubProvider()
    await _run(corpus_db, stub, question=QUESTION, answer="prior answer",
               stored_sources=stored)

    assert stub.last_system == ship30.load_rules()
    assert stub.last_append == ship30.load_skill()[1]
    # And the craft instructions are NOT smuggled into the user prompt, where
    # they would be indistinguishable from evidence.
    assert "Ship 30 Writing Skill" not in (stub.last_prompt or "")


# --------------------------------------------------------------------------
# Evidence carry-over
# --------------------------------------------------------------------------
async def test_prior_answer_is_framing_and_never_quotable(corpus_db, stored):
    """The previous answer is in the prompt, explicitly fenced off.

    Safety here is structural rather than prompted: the grounding haystack is
    built from evidence only, so a model quoting the prior answer verbatim gets
    flagged. The label just makes the intent legible to the model too.
    """
    stub = StubProvider()
    await _run(corpus_db, stub, question=QUESTION,
               answer="An earlier answer.", stored_sources=stored)

    prompt = stub.last_prompt or ""
    assert "An earlier answer." in prompt
    assert "NOT quotable" in prompt


async def test_no_evidence_at_all_is_refused_distinctly(corpus_db):
    """An abstention has nothing to write from, and says so in those terms.

    Kept separate from the stale-card refusal below because the two have
    different causes and different fixes. The route rejects this case first;
    this is the module-level backstop, so the rule holds for any caller.
    """
    with pytest.raises(EvidenceUnavailable) as exc:
        await ship30.assemble_evidence(
            corpus_db, stored_sources=[], question=QUESTION)
    assert "cited no evidence" in str(exc.value)


async def test_sources_without_chunk_ids_are_refused(corpus_db):
    """A pre-Phase-6 turn cannot be reconstructed, so it is not guessed at."""
    stale = [{"label": "E1", "source_id": "x", "similarity": 0.5}]
    with pytest.raises(EvidenceUnavailable):
        await ship30.assemble_evidence(
            corpus_db, stored_sources=stale, question=QUESTION)


async def test_carried_evidence_keeps_its_position(corpus_db):
    """E4. [E1] must mean in the essay what it meant in the answer.

    Position IS the label (agent.cite_label is 1-indexed by position), so this
    is what stops an essay's citations from silently renumbering.
    """
    from app.retrieval import retrieve
    original = await retrieve(corpus_db, QUESTION, 2)
    assert len(original) == 2, "corpus did not return enough evidence"

    from app.agent import source_summaries
    stored = source_summaries(original)

    assembled = await ship30.assemble_evidence(
        corpus_db, stored_sources=stored, question=QUESTION)

    assert assembled.carried == 2
    assert [e.chunk_id for e in assembled.items[:2]] == \
        [e.chunk_id for e in original]


async def test_topped_up_evidence_adds_no_duplicates(corpus_db):
    """E5. The top-up must extend the set, never repeat it."""
    from app.agent import source_summaries
    from app.retrieval import retrieve

    stored = source_summaries(await retrieve(corpus_db, QUESTION, 2))
    assembled = await ship30.assemble_evidence(
        corpus_db, stored_sources=stored, question=QUESTION, k=5)

    ids = [e.chunk_id for e in assembled.items]
    assert len(ids) == len(set(ids)), "top-up duplicated a carried chunk"
    assert len(ids) <= 5


async def test_topup_goes_deeper_on_an_episode_specific_question(corpus_db):
    """The per-source cap must not starve an essay of material.

    RETRIEVAL_MAX_PER_SOURCE=2 is a DIVERSITY rule for three-chunk answers. On
    a question answered by a single episode it is also the only thing capping
    the evidence, so an essay would get no more to write from than the answer
    had -- defeating the top-up entirely, in the common case of a specific
    question. Measured: cap=2 yields 2 chunks here, cap=4 yields 4, and the
    two it was withholding score ~0.66, far above the 0.40 floor.
    """
    from app.config import get_settings
    from app.retrieval import retrieve

    settings = get_settings()
    tight = await retrieve(corpus_db, QUESTION, settings.essay_retrieval_k,
                           max_per_source=settings.retrieval_max_per_source)
    roomy = await retrieve(corpus_db, QUESTION, settings.essay_retrieval_k,
                           max_per_source=settings.essay_max_per_source)

    assert settings.essay_max_per_source > settings.retrieval_max_per_source
    assert len(roomy) >= len(tight), "the essay cap must never narrow the set"
    # Whatever the extra chunks are, they cleared the calibrated floor -- the
    # cap was relaxed, the quality bar was not.
    assert all(e.similarity >= settings.retrieval_min_similarity for e in roomy)


async def test_essay_sources_prefix_matches_the_answers(corpus_db):
    """E4, end to end: the reader sees the same cards, in the same order."""
    from app.agent import source_summaries
    from app.retrieval import retrieve

    stored = source_summaries(await retrieve(corpus_db, QUESTION, 2))
    result = await _run(corpus_db, StubProvider(), question=QUESTION,
                        answer="prior", stored_sources=stored)

    essay_cards = result["sources"]["sources"]
    assert essay_cards[:2] == stored
    assert [c["label"] for c in essay_cards] == \
        [f"E{i + 1}" for i in range(len(essay_cards))]
    assert result["sources"]["carried"] == 2


# --------------------------------------------------------------------------
# Verification is mandatory, and unchanged
# --------------------------------------------------------------------------
async def test_every_essay_is_verified(corpus_db, stored):
    """V1. Not conditional on provider, model, length or configuration."""
    result = await _run(corpus_db, StubProvider("# T\n\nPlain prose."),
                        question=QUESTION, answer="a", stored_sources=stored)
    assert result["grounding"]["verdict"] in {"PASS", "FAIL"}
    assert "grounding" in result and result["complete"]["trustworthy"] in {True, False}


async def test_fabricated_straight_quote_in_an_essay_is_caught(corpus_db):
    from app.agent import source_summaries
    from app.retrieval import retrieve
    stored = source_summaries(await retrieve(corpus_db, QUESTION, 2))

    essay = ('# Growth\n\nIntro paragraph with real substance here.\n\n'
             '## A section\n\nThe guest said "a sentence that appears in no '
             'transcript anywhere at all". [E1]\n\n- a bullet\n- another\n')
    result = await _run(corpus_db, StubProvider(essay), question=QUESTION,
                        answer="a", stored_sources=stored)

    assert result["grounding"]["fabricated_quotes"], "straight-quoted fabrication missed"
    assert result["complete"]["trustworthy"] is False


async def test_fabricated_curly_quote_in_an_essay_is_caught(corpus_db):
    """The Phase 1 blind spot, at essay length -- where it originally happened.

    qwen3's Ship 30 essay was recorded as clean because the old harness matched
    only ASCII quotes. This is that exact shape of output.
    """
    from app.agent import source_summaries
    from app.retrieval import retrieve
    stored = source_summaries(await retrieve(corpus_db, QUESTION, 2))

    essay = ("# Growth\n\nIntro paragraph with real substance here.\n\n"
             "## A section\n\nShe put it memorably: “wholly invented words "
             "that appear in no episode”. [E1]\n")
    result = await _run(corpus_db, StubProvider(essay), question=QUESTION,
                        answer="a", stored_sources=stored)

    assert result["grounding"]["fabricated_quotes"], "curly-quoted fabrication missed"
    assert result["complete"]["trustworthy"] is False


async def test_invented_citation_tag_in_an_essay_is_caught(corpus_db):
    from app.agent import source_summaries
    from app.retrieval import retrieve
    stored = source_summaries(await retrieve(corpus_db, QUESTION, 2))

    result = await _run(corpus_db, StubProvider("# T\n\nA claim. [E99]"),
                        question=QUESTION, answer="a", stored_sources=stored)
    assert "E99" in result["grounding"]["invalid_tags"]
    assert result["complete"]["trustworthy"] is False


async def test_a_genuinely_clean_essay_passes(corpus_db):
    """V5. A detector that flags everything is as useless as one that flags nothing."""
    evidence = _evidence(2)
    from app.agent import source_summaries
    stored = source_summaries(evidence)

    async def fake_rehydrate(db, ids, similarities=None):
        return evidence

    import app.ship30 as mod
    original = mod.evidence_by_chunk_ids
    mod.evidence_by_chunk_ids = fake_rehydrate
    try:
        essay = (f'# Retention\n\nOpening line.\n\n'
                 f'He said "{EVIDENCE_TEXT}" [E1] and she agreed '
                 f'“{EVIDENCE_TEXT}” [E2].\n')
        result = await _run(corpus_db, StubProvider(essay), question=QUESTION,
                            answer="a", stored_sources=stored, k=2)
    finally:
        mod.evidence_by_chunk_ids = original

    assert result["grounding"]["grounded"] is True, result["grounding"]
    assert result["grounding"]["quotes_found"] == 2, "both styles must be examined"


# --------------------------------------------------------------------------
# Word target: measured and reported, never enforced
# --------------------------------------------------------------------------
def test_word_count_matches_the_phase1_measurement_convention():
    """The number this product reports must mean what bench.json's means."""
    assert ship30.word_count("one two three") == 3
    assert ship30.word_count("  padded   spacing  here ") == 3
    assert ship30.word_count("") == 0


async def test_nothing_is_truncated_to_hit_the_target(corpus_db, stored):
    """W2. Cutting to length would sever quotes and tags mid-sentence."""
    long_essay = "# T\n\n" + " ".join(f"word{i}" for i in range(3000))
    result = await _run(corpus_db, StubProvider(long_essay), question=QUESTION,
                        answer="a", stored_sources=stored)

    assert result["complete"]["markdown"] == result["delta"].strip()
    assert result["complete"]["word_count"] > 1500
    assert result["complete"]["within_target"] is False, "a miss must be reported"


async def test_within_target_is_reported_for_an_on_length_essay(corpus_db, stored):
    body = " ".join(f"word{i}" for i in range(1200))
    result = await _run(corpus_db, StubProvider(f"# T\n\n{body}"),
                        question=QUESTION, answer="a", stored_sources=stored)
    assert result["complete"]["within_target"] is True
    assert result["complete"]["target_words"] == 1250


async def test_reported_word_count_is_the_stored_markdown(corpus_db, stored):
    """W1. One number, one definition, one source of truth."""
    result = await _run(corpus_db, StubProvider("# T\n\nOne two three four."),
                        question=QUESTION, answer="a", stored_sources=stored)
    complete = result["complete"]
    assert complete["word_count"] == ship30.word_count(complete["markdown"])


def test_title_is_read_never_invented():
    assert ship30.title_of("# Distribution Is the Game\n\nBody.") == \
        "Distribution Is the Game"
    # No H1 means no title. We do not make one up on the essay's behalf.
    assert ship30.title_of("Body with no heading at all.") is None


def test_blockquotes_are_counted_not_treated_as_quotation():
    """The measured reason extraction was NOT extended -- see the module note.

    The real Phase 1 essay's blockquotes are a sources list, so treating them
    as pull-quotes flags honest work. The count is surfaced instead.
    """
    md = "# T\n\n> [E1] Someone — \"An Episode\" (2025-01-01)\n\nBody.\n"
    assert ship30.blockquote_lines(md) == 1
    # And they remain outside the verdict.
    from app.grounding import extract_quotes
    assert "[E1] Someone" not in " ".join(extract_quotes(md))


# --------------------------------------------------------------------------
# Provider selection stays configuration
# --------------------------------------------------------------------------
async def test_provider_identity_is_reported_not_branched_on(corpus_db, stored):
    result = await _run(corpus_db, StubProvider(), question=QUESTION,
                        answer="a", stored_sources=stored)
    assert result["complete"]["provider"] == "stub"
    assert result["complete"]["model"] == "stub-model"


def test_ship30_module_has_no_provider_conditionals():
    """Skill 04: business logic must not branch on provider identity."""
    import inspect

    src = inspect.getsource(ship30)
    for forbidden in ('== "ollama"', "== 'ollama'",
                      '== "deepseek"', "== 'deepseek'"):
        assert forbidden not in src, f"provider branch found: {forbidden}"


def test_ship30_has_no_pi_specific_logic():
    """The agent framework stays behind the seam, as it does for answers."""
    import inspect

    src = inspect.getsource(ship30)
    assert "PiRuntime" not in src
    assert "subprocess" not in src


# --------------------------------------------------------------------------
# Packaging: the skill has to exist in the deployment the evaluator runs
# --------------------------------------------------------------------------
class TestSkillIsShipped:
    """`.claude/` is in .dockerignore, so the authored skill file is NOT in the
    runtime image by default -- and a runtime read of it would work on the host
    dev loop while failing in `docker compose up`. That is the worst shape of
    bug available here: invisible until the graded environment.

    These assert the two lines that close it. They read repo files that are
    deliberately absent from the image, so in-container they skip loudly rather
    than pretending to pass.
    """

    @staticmethod
    def _repo_file(name: str):
        from pathlib import Path
        path = Path(__file__).parents[2] / name
        if not path.is_file():
            pytest.skip(
                f"{name} is not present -- this is the container image, where "
                f"build files are deliberately absent. Run on the host.")
        return path.read_text(encoding="utf-8")

    def test_dockerignore_re_includes_the_skill(self):
        text = self._repo_file(".dockerignore")
        assert ".claude/" in text, "test premise: .claude is excluded"
        assert f"!.claude/skills/{ship30.SKILL_NAME}/" in text, (
            "the skill is excluded from the build context, so the Dockerfile "
            "COPY below cannot find it")

    def test_dockerfile_copies_the_skill_next_to_the_code(self):
        text = self._repo_file("backend/Dockerfile")
        assert f".claude/skills/{ship30.SKILL_NAME}/SKILL.md" in text
        assert f"app/skills/{ship30.SKILL_NAME}/SKILL.md" in text, (
            "the skill must land where ship30._SKILL_CANDIDATES looks first")

    def test_the_container_lookup_path_is_first(self):
        """Order matters: the image copy must win over any repo copy."""
        assert str(ship30._SKILL_CANDIDATES[0]).replace("\\", "/").endswith(
            f"app/skills/{ship30.SKILL_NAME}/SKILL.md")
