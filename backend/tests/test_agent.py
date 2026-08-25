"""Agent layer -- the guarantees that make an answer trustworthy.

The properties here are the ones a user is actually trusting:

  - no evidence -> no answer, and the model is never even called
  - every generated answer is verified, unconditionally
  - the model cannot introduce a source that retrieval did not return
  - provider choice is configuration, never a branch in business logic

Most tests use a stub provider so they are fast, deterministic, and provable
without a running model. A stub is the right tool here precisely because we
are testing what happens when a model misbehaves -- real models cannot be
asked to fabricate on cue.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.agent import (
    ABSTENTION, AnswerResult, answer_question, build_prompt, cite_label,
    source_summaries, stream_answer,
)
from app.providers import ModelProvider
from app.retrieval import Evidence

pytestmark = pytest.mark.usefixtures("corpus_ready", "ollama_ready")

QUESTION = "How does Duolingo use streaks to improve retention?"
UNSUPPORTED = "How do I make a sourdough starter from scratch?"


class StubProvider(ModelProvider):
    """Says exactly what the test tells it to, and records that it ran.

    Deliberately overrides stream() to bypass the Pi subprocess: these tests
    are about agent-layer behaviour (abstention, grounding, citation
    validation), and they must be able to make a model misbehave on cue,
    which a real model cannot be asked to do. Pi's own boundary is covered
    in tests/test_pi_runtime.py.
    """

    name = "stub"

    def __init__(self, text: str = "An answer. [E1]") -> None:
        self.text = text
        self.calls = 0
        self.last_prompt: str | None = None

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

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        self.calls += 1
        self.last_prompt = prompt
        for word in self.text.split(" "):
            yield word + " "


def _evidence(text: str = "Streaks work because loss aversion is powerful.",
              n: int = 1) -> list[Evidence]:
    return [
        Evidence(
            source_id="jackson-shuttleworth", source_title="Duolingo streaks",
            speaker="Jackson Shuttleworth", source_url="https://youtu.be/x",
            transcript_id=f"t{i}", chunk_id=f"c{i}", publish_date=None,
            chunk_index=i, guest="Jackson Shuttleworth", text=text,
            start_seconds=10 * i, end_seconds=10 * i + 5, similarity=0.7,
            citation_url=f"https://youtu.be/x?t={10 * i}",
        )
        for i in range(n)
    ]


# --------------------------------------------------------------------------
# Unsupported -> no evidence -> no grounded answer, model NEVER called
# --------------------------------------------------------------------------
async def test_unsupported_question_abstains(corpus_db):
    stub = StubProvider("I definitely know this from memory.")
    result = await answer_question(corpus_db, UNSUPPORTED, provider=stub)

    assert result.abstained is True
    assert result.supported is False
    assert result.trustworthy is False
    assert result.answer == ABSTENTION
    assert result.evidence == []


async def test_abstention_never_invokes_the_model(corpus_db):
    """Structural, not prompted.

    If the model were called and merely asked to refuse, refusal would be a
    request the model could decline. Here there is no generation step at all.
    """
    stub = StubProvider("Here is a confident unsourced answer.")
    await answer_question(corpus_db, UNSUPPORTED, provider=stub)
    assert stub.calls == 0, "the model was invoked despite having no evidence"


async def test_abstention_carries_no_sources(corpus_db):
    result = await answer_question(corpus_db, UNSUPPORTED,
                                   provider=StubProvider())
    assert result.sources == []
    assert "[E" not in result.answer


async def test_answerable_question_does_invoke_the_model(corpus_db):
    """The counterpart -- abstention must not be the answer to everything."""
    stub = StubProvider("Streaks drive retention. [E1]")
    result = await answer_question(corpus_db, QUESTION, provider=stub)
    assert stub.calls == 1
    assert result.abstained is False
    assert result.supported is True


# --------------------------------------------------------------------------
# Grounding verification is MANDATORY
# --------------------------------------------------------------------------
async def test_every_answer_is_verified(corpus_db):
    result = await answer_question(corpus_db, QUESTION,
                                   provider=StubProvider("Plain answer. [E1]"))
    assert result.grounding is not None
    assert result.grounding.verdict in {"PASS", "FAIL"}


async def test_invented_citation_tag_is_caught(corpus_db):
    """The model cites [E9] when only a few evidence items exist."""
    stub = StubProvider("Retention improves because of streaks [E9].")
    result = await answer_question(corpus_db, QUESTION, provider=stub)

    assert "E9" in result.grounding.invalid_tags
    assert result.grounding.grounded is False
    assert result.trustworthy is False, "an invented citation must not read as trustworthy"


async def test_fabricated_quote_is_caught(corpus_db):
    stub = StubProvider(
        'He said "this exact sentence appears in no transcript anywhere". [E1]')
    result = await answer_question(corpus_db, QUESTION, provider=stub)

    assert result.grounding.fabricated_quotes
    assert result.trustworthy is False


async def test_ungrounded_answer_is_reported_not_hidden(corpus_db):
    """We surface it. Silently passing it through is the failure mode."""
    stub = StubProvider('"a quote nobody ever said in this corpus at all" [E7]')
    result = await answer_question(corpus_db, QUESTION, provider=stub)

    d = result.to_dict()
    assert d["trustworthy"] is False
    assert d["grounding"]["verdict"] == "FAIL"
    assert d["grounding"]["fabricated_quotes"]
    assert d["grounding"]["invalid_tags"] == ["E7"]


# --------------------------------------------------------------------------
# Curly AND straight quotes must both be checked
# --------------------------------------------------------------------------
async def test_straight_quote_fabrication_is_caught(corpus_db):
    stub = StubProvider(
        'The guest said "wholly invented words that appear in no episode". [E1]')
    result = await answer_question(corpus_db, QUESTION, provider=stub)
    assert result.grounding.fabricated_quotes, "straight-quoted fabrication missed"


async def test_curly_quote_fabrication_is_caught(corpus_db):
    """The exact blind spot that made Phase 1 report a false PASS.

    The old spike harness matched only ASCII quotes, so qwen3's curly-quoted
    spans were never examined and four fabrications were reported as zero.
    """
    stub = StubProvider(
        "The guest said “wholly invented words that appear in no episode”. [E1]")
    result = await answer_question(corpus_db, QUESTION, provider=stub)
    assert result.grounding.fabricated_quotes, "curly-quoted fabrication missed"


async def test_both_quote_styles_pass_when_genuine(corpus_db):
    """Both styles must also be accepted when the words are real.

    A detector that flags everything is as useless as one that flags nothing.
    """
    real = "Streaks work because loss aversion is powerful."
    evidence = _evidence(real)
    from app.grounding import verify_answer

    for quoted in (f'He said "{real}" [E1]',
                   f"He said “{real}” [E1]"):
        report = verify_answer(quoted, evidence)
        assert report.grounded, f"real quote wrongly flagged: {quoted}"


async def test_mixed_styles_in_one_answer(corpus_db):
    real = "Streaks work because loss aversion is powerful."
    from app.grounding import verify_answer
    answer = (f'One said "{real}" and another said '
              f"“this part was never said by anyone” [E1]")
    report = verify_answer(answer, _evidence(real))
    assert len(report.fabricated_quotes) == 1
    assert report.quotes_found == 2


# --------------------------------------------------------------------------
# The model cannot introduce a source
# --------------------------------------------------------------------------
async def test_sources_come_from_retrieval_not_the_model(corpus_db):
    """Source cards are built from stored rows, so they cannot be fabricated
    even when the answer text is."""
    stub = StubProvider("Invented claim about Acme Corp and Jane Doe. [E1]")
    result = await answer_question(corpus_db, QUESTION, provider=stub)

    for card, ev in zip(result.sources, result.evidence):
        assert card["source_id"] == ev.source_id
        assert card["citation_url"] == ev.citation_url
        assert card["speaker"] == ev.speaker
    assert all("Acme" not in str(c) for c in result.sources)


async def test_source_labels_are_one_indexed_and_match_the_prompt(corpus_db):
    evidence = _evidence(n=3)
    prompt = build_prompt("q", evidence)
    labels = [s["label"] for s in source_summaries(evidence)]

    assert labels == ["E1", "E2", "E3"]
    assert cite_label(0) == "E1"
    for label in labels:
        assert f"[{label}]" in prompt


async def test_prompt_contains_only_the_given_evidence(corpus_db):
    stub = StubProvider()
    await answer_question(corpus_db, QUESTION, provider=stub)
    prompt = stub.last_prompt

    assert "ONLY the provided evidence" in prompt
    assert "NEVER cite a number that was not provided" in prompt
    assert "VERBATIM" in prompt
    # No stray evidence numbers beyond what was supplied.
    assert "[E9]" not in prompt


# --------------------------------------------------------------------------
# Provider selection stays configuration-driven
# --------------------------------------------------------------------------
async def test_provider_identity_is_reported_not_branched_on(corpus_db):
    stub = StubProvider("ok [E1]")
    result = await answer_question(corpus_db, QUESTION, provider=stub)
    assert result.provider == "stub"
    assert result.model == "stub-model"


async def test_agent_uses_configured_provider_when_none_passed(corpus_db,
                                                               monkeypatch):
    """No caller names a provider; configuration decides."""
    import app.agent as agent_mod
    seen = {}

    def fake_get_provider(name=None):
        seen["called"] = True
        return StubProvider("ok [E1]")

    monkeypatch.setattr(agent_mod, "get_provider", fake_get_provider)
    result = await answer_question(corpus_db, QUESTION)
    assert seen.get("called"), "agent did not consult provider configuration"
    assert result.provider == "stub"


def test_agent_module_has_no_provider_conditionals():
    """Skill 04: business logic must not branch on provider identity."""
    import inspect

    import app.agent as agent_mod
    src = inspect.getsource(agent_mod)
    for forbidden in ('== "ollama"', "== 'ollama'",
                      '== "deepseek"', "== 'deepseek'"):
        assert forbidden not in src, f"provider branch found: {forbidden}"


# --------------------------------------------------------------------------
# Streaming variant keeps the same guarantees
# --------------------------------------------------------------------------
async def test_stream_abstains_without_calling_model(corpus_db):
    stub = StubProvider("should never be produced")
    events = [(e, p) async for e, p in
              stream_answer(corpus_db, UNSUPPORTED, provider=stub)]

    kinds = [e for e, _ in events]
    assert stub.calls == 0
    assert kinds[0] == "sources"
    assert kinds[-1] == "complete"
    text = "".join(p["text"] for e, p in events if e == "delta")
    assert text.strip() == ABSTENTION


async def test_stream_emits_grounding_before_complete(corpus_db):
    stub = StubProvider("answer [E1]")
    kinds = [e async for e, _ in
             stream_answer(corpus_db, QUESTION, provider=stub)]
    assert kinds.index("grounding") < kinds.index("complete")
    assert kinds.index("sources") < kinds.index("delta")


async def test_stream_flags_untrustworthy_output(corpus_db):
    stub = StubProvider("citing nothing real [E42]")
    events = [(e, p) async for e, p in
              stream_answer(corpus_db, QUESTION, provider=stub)]
    complete = [p for e, p in events if e == "complete"][0]
    grounding = [p for e, p in events if e == "grounding"][0]

    assert complete["trustworthy"] is False
    assert grounding["invalid_tags"] == ["E42"]


# --------------------------------------------------------------------------
# Real model, end to end
# --------------------------------------------------------------------------
async def test_real_local_model_produces_a_grounded_answer(corpus_db):
    """The mandated demo path: Ollama, real corpus, verified output."""
    result = await answer_question(corpus_db, QUESTION)

    assert result.abstained is False
    assert result.supported is True
    assert result.answer.strip()
    assert result.provider == "ollama"
    assert result.sources
    # The hard guarantee: no citation may point at evidence that does not exist.
    assert result.grounding.invalid_tags == [], (
        f"model invented citations: {result.grounding.invalid_tags}")
