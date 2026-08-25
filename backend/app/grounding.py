"""Quote and citation verification.

Promoted from `spike/verify_quotes.py`, which was written in Phase 1 to check
whether local models were inventing quotes. It earned promotion by catching a
real one: `llama3.2:3b` fabricated quoted text in 2 of 3 tests, and that
finding is why `qwen3:4b-instruct` is the locked local model.

This module answers one question: **does every quotation and every citation
tag in an answer actually correspond to retrieved evidence?** It is the
mechanical half of the Grounded Answer Rate metric -- the part that does not
require a human to read the transcript.

What it is NOT: a fact-checker. It verifies that quoted spans appear in the
evidence and that citation tags point at real evidence. A model can still
misrepresent evidence it quotes correctly. Phase 4 uses this as a gate on
generated answers; it does not replace reading them.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

log = logging.getLogger("app.grounding")

# A quoted span is checked when it is at least this many words. Single words
# in quotes are emphasis or scare-quotes ("retention"), not attribution.
#
# This used to be a 25-CHARACTER floor, inherited from the Phase 1 spike, and
# it hid a real fabrication: DeepSeek attributed "golden goose" (12 chars) to
# an episode that never says it, and grounding reported PASS because the span
# was too short to examine. Punchy phrases are exactly what a model invents
# and a reader repeats, so length is the wrong axis -- word count is not.
MIN_QUOTE_WORDS = 2

# Straight and curly double quotes; models emit both.
QUOTE_RE = re.compile(r'["“]([^"“”]+)["”]')

# Citation tags of the form [E1], [E2] ...
TAG_RE = re.compile(r"\[(E\d+)\]")


def normalize(text: str) -> str:
    """Fold the differences that are not fabrication.

    Models routinely restyle punctuation and whitespace while quoting
    faithfully. Curly apostrophes, doubled spaces and case are therefore
    normalised away; the WORDS are what must match.
    """
    text = unicodedata.normalize("NFKC", text)
    text = (text.replace("’", "'").replace("‘", "'")
                .replace("“", '"').replace("”", '"')
                .replace("—", "-").replace("–", "-"))
    return re.sub(r"\s+", " ", text.lower()).strip()


@dataclass
class QuoteReport:
    quotes_found: int = 0
    fabricated_quotes: list[str] = field(default_factory=list)
    tags_found: list[str] = field(default_factory=list)
    invalid_tags: list[str] = field(default_factory=list)

    @property
    def grounded(self) -> bool:
        """True only if nothing was invented. This is the trust property."""
        return not self.fabricated_quotes and not self.invalid_tags

    @property
    def verdict(self) -> str:
        return "PASS" if self.grounded else "FAIL"

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "grounded": self.grounded,
            "quotes_found": self.quotes_found,
            "fabricated_quotes": self.fabricated_quotes,
            "tags_found": self.tags_found,
            "invalid_tags": self.invalid_tags,
        }


# Fields of an Evidence the model legitimately sees, and may therefore quote.
_QUOTABLE_FIELDS = ("text", "source_title", "speaker", "guest")


def _field(item, name: str):
    """Read one field from an Evidence dataclass or a plain dict.

    Both shapes are real: retrieval hands over dataclasses, while the stored
    Phase 1 evidence sets are JSON. One tiny accessor is clearer than a
    per-iteration lambda that quietly encodes the same assumption.
    """
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _haystack(evidence) -> str:
    """Everything the model legitimately saw, as one normalised string.

    Titles, speakers and guest names are included because they are part of the
    evidence block handed to the model. Quoting an episode title is not
    fabrication.
    """
    parts: list[str] = []
    for item in evidence:
        for key in _QUOTABLE_FIELDS:
            value = _field(item, key)
            if value:
                parts.append(str(value))
    return normalize(" ".join(parts))


def extract_quotes(answer: str) -> list[str]:
    """Quoted spans worth verifying: at least MIN_QUOTE_WORDS words."""
    return [q for q in QUOTE_RE.findall(answer or "")
            if len(q.split()) >= MIN_QUOTE_WORDS]


def verify_answer(answer: str, evidence) -> QuoteReport:
    """Check an answer's quotes and citation tags against its evidence.

    `evidence` is a sequence of Evidence objects or dicts. Citation tags are
    1-indexed by position: [E1] is the first item.
    """
    report = QuoteReport()
    if not answer:
        return report

    haystack = _haystack(evidence)
    valid_tags = {f"E{i}" for i in range(1, len(evidence) + 1)}

    for quote in extract_quotes(answer):
        report.quotes_found += 1
        needle = normalize(quote).strip(".,;:!? ")
        if needle and needle not in haystack:
            report.fabricated_quotes.append(quote)

    tags = TAG_RE.findall(answer)
    report.tags_found = sorted(set(tags))
    report.invalid_tags = sorted(set(tags) - valid_tags)

    if not report.grounded:
        # Loud by design: a fabricated citation is the failure this product
        # most needs to detect, so it must never be a silent return value.
        log.warning("grounding_failed", extra={
            "fabricated_quotes": len(report.fabricated_quotes),
            "invalid_tags": report.invalid_tags,
            "evidence_count": len(evidence),
            "outcome": "error"})

    return report
