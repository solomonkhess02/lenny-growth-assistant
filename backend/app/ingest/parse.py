"""Transcript parsing: frontmatter, speaker turns, timestamps.

This module exists because of a real defect. The Phase 1 parser accepted only
`HH:MM:SS` timestamps, so `casey-winters.md` produced **zero turns and no
error** -- 10,457 words silently excluded from the corpus. A retrieval system
that quietly ingests 19 of 20 files is worse than one that crashes, because it
answers confidently from a corpus nobody knows is incomplete.

Measured across the curated 20 (2026-08-25):

  - `MM:SS` is 15% of the corpus, not one outlier: gibson-biddle (231 turns),
    merci-grace (121), casey-winters (78). Under the old regex those three
    would have contributed 430 turns and ~33,400 words of nothing.
  - 4 of 20 episodes have MORE THAN TWO speakers (dan-hockenmaier has five).
    The host+guest assumption is wrong on 1 in 5 episodes.

So: both formats are first-class, speaker handling is N-way, and an empty
parse is an exception that names the file rather than an empty list.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date

from ..errors import ValidationFailed

# A turn header is a line like "Lenny Rachitsky (01:23:45):" or "Casey (12:34):".
# Both timestamp widths are matched by one regex -- the alternative (try one,
# fall back to the other) is how the Phase 1 defect happened in the first place.
TURN_RE = re.compile(
    r"^(?P<speaker>[A-Z][A-Za-z.'\- ]+?) \((?P<ts>(?:\d{1,2}:)?\d{1,2}:\d{2})\):\s*$",
    re.MULTILINE,
)

# Turns shorter than this are backchannel ("Yeah", "Right", "Totally") and are
# merged forward into the next substantive turn. Measured over 336 turns in
# Phase 2A: 40% of turns are under 20 words and carry no retrievable content.
BACKCHANNEL_MAX_WORDS = 20

# Host name aliasing. The same person appears as both across the corpus, and an
# unnormalised speaker field would split his attributions in two.
SPEAKER_ALIASES = {
    "lenny": "Lenny Rachitsky",
    "lenny rachitsky": "Lenny Rachitsky",
}


@dataclass(frozen=True)
class Turn:
    speaker: str
    start_seconds: int
    text: str

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass(frozen=True)
class Transcript:
    slug: str
    guest: str
    title: str
    youtube_url: str
    video_id: str
    publish_date: date | None
    channel: str
    keywords: list[str]
    content_hash: str
    word_count: int
    turns: list[Turn]

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def speakers(self) -> list[str]:
        return sorted({t.speaker for t in self.turns})


def parse_timestamp(ts: str) -> int:
    """`MM:SS` or `HH:MM:SS` -> absolute seconds.

    Returning seconds (not the original string) is what makes a citation
    verifiable: `youtube_url + "&t={start_seconds}"` deep-links a human to the
    exact moment the sentence was said.
    """
    parts = [int(p) for p in ts.split(":")]
    if len(parts) == 2:
        m, s = parts
        return m * 60 + s
    if len(parts) == 3:
        h, m, s = parts
        return h * 3600 + m * 60 + s
    raise ValidationFailed(f"Unparseable timestamp {ts!r}.")


def normalize_speaker(raw: str) -> str:
    """Collapse known aliases; otherwise clean whitespace and keep the name.

    Deliberately NOT a whitelist. 4 of 20 episodes have a third, fourth or
    fifth speaker, and dropping an unrecognised name would silently
    misattribute their words to someone else -- the exact class of failure
    this project treats as unacceptable.
    """
    cleaned = " ".join(raw.split())
    return SPEAKER_ALIASES.get(cleaned.lower(), cleaned)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from the body.

    Hand-rolled rather than pulling PyYAML: we need exactly six scalar fields
    plus one string list, the format is machine-generated and stable, and
    skill 01 prefers few dependencies. Unknown keys are ignored, not an error.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw, body = text[3:end], text[end + 4:]

    meta: dict = {}
    key: str | None = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("- ") and key:
            # A YAML list follows its key on later lines, so the key was
            # already recorded as an empty string. setdefault() would keep
            # that string and silently discard every list item, which is how
            # `keywords` came back empty. Promote to a list explicitly.
            if not isinstance(meta.get(key), list):
                meta[key] = []
            meta[key].append(line[2:].strip())
            continue
        m = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            if value:
                meta[key] = value.strip("'\"")
            else:
                meta[key] = ""
        elif key and isinstance(meta.get(key), str):
            # Continuation of a folded multi-line scalar (title, description).
            meta[key] = f"{meta[key]} {line.strip()}".strip()
    return meta, body


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None


def parse_transcript(slug: str, raw: bytes) -> Transcript:
    """Parse one transcript file. Raises rather than returning an empty parse.

    `duration` / `duration_seconds` are read but deliberately DISCARDED.
    Phase 2A proved they describe the linked YouTube *clip*, not the
    transcript: casey-winters claims 99 seconds while its transcript runs to
    (54:50) = 3,290 s. Storing it would invite a wrong citation.
    """
    text = raw.decode("utf-8")
    meta, body = _parse_frontmatter(text)

    matches = list(TURN_RE.finditer(body))
    if not matches:
        # The Phase 1 defect, made loud. The file is named because "0 turns"
        # without an identity is not actionable at ingest time.
        raise ValidationFailed(
            f"Transcript '{slug}' parsed to ZERO speaker turns. Expected lines "
            f"like 'Speaker (MM:SS):' or 'Speaker (HH:MM:SS):'. Refusing to "
            f"ingest -- a silently empty transcript would leave the corpus "
            f"incomplete with no error."
        )

    turns: list[Turn] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[m.end():end].strip()
        if not content:
            continue
        turns.append(Turn(
            speaker=normalize_speaker(m.group("speaker")),
            start_seconds=parse_timestamp(m.group("ts")),
            text=" ".join(content.split()),
        ))

    if not turns:
        raise ValidationFailed(
            f"Transcript '{slug}' has {len(matches)} turn headers but every one "
            f"has empty content. Refusing to ingest."
        )

    return Transcript(
        slug=slug,
        guest=meta.get("guest", "").strip() or "Unknown",
        title=meta.get("title", "").strip() or slug,
        youtube_url=meta.get("youtube_url", "").strip(),
        video_id=meta.get("video_id", "").strip(),
        publish_date=_parse_date(meta.get("publish_date", "")),
        channel=meta.get("channel", "").strip(),
        keywords=[k for k in meta.get("keywords", []) if k] if isinstance(
            meta.get("keywords"), list) else [],
        content_hash=hashlib.sha256(raw).hexdigest(),
        word_count=len(body.split()),
        turns=turns,
    )


def merge_backchannel(turns: list[Turn]) -> list[Turn]:
    """Fold sub-20-word turns into the following substantive turn.

    A chunk whose text is "Yeah." is retrievable noise: it embeds to nothing
    useful and, if it ever surfaced as evidence, would attribute a substantive
    claim to a filler word.

    Speaker attribution is preserved by prefixing the merged speaker's name
    rather than discarding it -- skill 02 forbids losing speaker attribution,
    and a merged turn genuinely contains two voices. The resulting turn is
    stamped with the SUBSTANTIVE speaker, since that is who the retrievable
    content belongs to.
    """
    if not turns:
        return []

    out: list[Turn] = []
    pending: list[Turn] = []
    for turn in turns:
        if turn.word_count < BACKCHANNEL_MAX_WORDS:
            pending.append(turn)
            continue
        if pending:
            prefix = " ".join(f"{p.speaker}: {p.text}" for p in pending)
            turn = Turn(
                speaker=turn.speaker,
                start_seconds=pending[0].start_seconds,
                text=f"{prefix} {turn.speaker}: {turn.text}",
            )
            pending = []
        out.append(turn)

    # Trailing backchannel with nothing substantive after it: append to the
    # last real turn rather than dropping words on the floor.
    if pending:
        if out:
            tail = " ".join(f"{p.speaker}: {p.text}" for p in pending)
            last = out[-1]
            out[-1] = Turn(last.speaker, last.start_seconds, f"{last.text} {tail}")
        else:
            out = list(pending)
    return out
