"""Speaker-aware chunk packing.

Why not fixed token windows: they shred sentences mid-clause and destroy
speaker attribution, which skill 02 explicitly forbids. Why not whole
episodes: 15k words is far past the 8,192-token local context. Why not
semantic segmentation: it needs another model to justify itself at 20
episodes.

So chunks are built from whole speaker turns, packed to a token budget, with
overlap so an idea spanning a boundary is retrievable from either side.

Measured turn statistics behind the numbers (336 turns, Phase 2A):
mean 95 words, median 30, p90 294, max 1,353. p90 is ~390 tokens, which is
why the budget is 400 -- most turns fit whole, and only genuine monologues
need splitting.

Every chunk keeps enough to cite it: the dominant speaker, an inline
attribution for every voice in the text, and the real start/end timestamps.
Nothing here interpolates a timestamp it did not observe.
"""
from __future__ import annotations

from dataclasses import dataclass

from .parse import Turn

# 300,853 words -> ~400,000 tokens across the curated corpus. The 1.33 ratio
# is measured, not assumed (Phase 2A: 290,206 tokens / 218,200 words).
TOKENS_PER_WORD = 1.33

CHUNK_TARGET_TOKENS = 400
OVERLAP_RATIO = 0.25


def estimate_tokens(text: str) -> int:
    return int(len(text.split()) * TOKENS_PER_WORD)


@dataclass(frozen=True)
class Chunk:
    chunk_index: int
    speaker: str
    text: str
    start_seconds: int
    end_seconds: int
    token_estimate: int


def _turn_cost(turn: Turn) -> int:
    """Token cost of a turn *as rendered* -- inline `Speaker:` label included.

    Packing must budget with the same ruler the emitted chunk is measured by.
    Counting bare turn text while charging labelled text is how chunks drifted
    over budget without any single step looking wrong.
    """
    return estimate_tokens(f"{turn.speaker}: {turn.text}")


def _dominant_speaker(turns: list[Turn]) -> str:
    """The speaker contributing the most words to this chunk.

    A chunk can span several voices; skill 02 still requires a `speaker`
    field. Attributing it to whoever says the most is the honest summary,
    and every individual voice remains visible inline in the text, so a
    reader can always see who actually said a given sentence.
    """
    totals: dict[str, int] = {}
    for t in turns:
        totals[t.speaker] = totals.get(t.speaker, 0) + t.word_count
    # Sorted tie-break keeps chunking deterministic across runs.
    return max(sorted(totals), key=lambda s: totals[s])


def _render(turns: list[Turn]) -> str:
    """Inline speaker labels so attribution survives into the prompt itself."""
    return "\n".join(f"{t.speaker}: {t.text}" for t in turns)


def _split_long_turn(turn: Turn, budget: int) -> list[Turn]:
    """Split a single over-budget turn on word boundaries.

    Every fragment keeps the ORIGINAL turn's start_seconds. That is
    deliberate: we know when the turn began, and we do not know when any
    interior sentence was spoken. Interpolating a plausible-looking timestamp
    would manufacture precision that a human clicking the citation would find
    to be wrong.
    """
    words = turn.text.split()
    # Reserve room for the inline `Speaker:` label the fragment will carry,
    # so a split piece cannot come back over budget once rendered.
    label_cost = estimate_tokens(f"{turn.speaker}: ")
    max_words = max(1, int((budget - label_cost) / TOKENS_PER_WORD))
    stride = max(1, int(max_words * (1 - OVERLAP_RATIO)))

    out: list[Turn] = []
    for start in range(0, len(words), stride):
        piece = words[start:start + max_words]
        if not piece:
            break
        out.append(Turn(turn.speaker, turn.start_seconds, " ".join(piece)))
        if start + max_words >= len(words):
            break
    return out


def chunk_turns(
    turns: list[Turn],
    *,
    target_tokens: int = CHUNK_TARGET_TOKENS,
    overlap_ratio: float = OVERLAP_RATIO,
) -> list[Chunk]:
    """Pack consecutive turns into overlapping, speaker-attributed chunks."""
    if not turns:
        return []

    # Expand over-budget monologues first so packing only ever sees turns
    # that can actually fit.
    expanded: list[Turn] = []
    for t in turns:
        if _turn_cost(t) > target_tokens:
            expanded.extend(_split_long_turn(t, target_tokens))
        else:
            expanded.append(t)

    overlap_budget = target_tokens * overlap_ratio
    chunks: list[Chunk] = []
    current: list[Turn] = []
    current_tokens = 0

    def flush(next_start: int | None) -> list[Turn]:
        """Emit `current` as a chunk; return the turns to carry as overlap."""
        nonlocal chunks
        if not current:
            return []
        text = _render(current)
        chunks.append(Chunk(
            chunk_index=len(chunks),
            speaker=_dominant_speaker(current),
            text=text,
            start_seconds=current[0].start_seconds,
            # The chunk ends where the next turn begins. When there is no next
            # turn we fall back to the last turn's own start rather than
            # inventing a duration.
            end_seconds=next_start if next_start is not None
            else current[-1].start_seconds,
            token_estimate=estimate_tokens(text),
        ))
        carry: list[Turn] = []
        carried_tokens = 0
        for turn in reversed(current):
            tok = _turn_cost(turn)
            # Hard bound, with no "at least one turn" escape. That escape let a
            # single 400-token trailing turn become the overlap, so the next
            # chunk started full and reached 805 tokens -- 2x budget. A turn
            # too large to overlap simply does not overlap; it is already a
            # coherent unit on its own.
            if carried_tokens + tok > overlap_budget:
                break
            carry.insert(0, turn)
            carried_tokens += tok
        # Carrying the entire chunk forward would never make progress.
        return [] if len(carry) == len(current) else carry

    for turn in expanded:
        tok = _turn_cost(turn)
        if current and current_tokens + tok > target_tokens:
            current = flush(turn.start_seconds)
            current_tokens = sum(_turn_cost(t) for t in current)
        current.append(turn)
        current_tokens += tok

    flush(None)
    return chunks
