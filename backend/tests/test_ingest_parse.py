"""Parser tests -- eval cases 8, 9, 10.

These run OFFLINE against the three real transcripts committed in
`spike/evidence/`. No database, no Ollama, no network. That matters: the
parser is the layer that decides whether a transcript enters the corpus at
all, so its tests must never be the ones that get skipped.

The turn counts asserted below are measured facts about real files, not
round numbers. If a refactor changes them, the corpus changed meaning.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.errors import ValidationFailed
from app.ingest.parse import (
    BACKCHANNEL_MAX_WORDS, Turn, merge_backchannel, normalize_speaker,
    parse_timestamp, parse_transcript,
)

EVIDENCE = Path(__file__).parents[2] / "spike" / "evidence"


def _load(slug: str):
    return parse_transcript(slug, (EVIDENCE / f"{slug}.md").read_bytes())


# --------------------------------------------------------------------------
# Case 9 -- HH:MM:SS transcript
# --------------------------------------------------------------------------
def test_hhmmss_transcript_parses():
    t = _load("brian-balfour")
    assert t.turn_count == 161
    assert t.guest == "Brian Balfour"
    assert t.publish_date is not None and t.publish_date.year == 2025
    assert t.youtube_url.startswith("https://www.youtube.com/watch?v=")
    # Last turn is deep into a 1h29m episode -- proves hours were not truncated.
    assert t.turns[-1].start_seconds > 3600


# --------------------------------------------------------------------------
# Case 8 -- MM:SS transcript. THE Phase 1 regression.
# --------------------------------------------------------------------------
def test_mmss_transcript_parses():
    """casey-winters yielded ZERO turns in Phase 1 and was silently dropped."""
    t = _load("casey-winters")
    assert t.turn_count == 78, "the MM:SS silent-drop defect has returned"
    assert t.word_count > 10_000
    # 54:50 is 3,290s -- if MM:SS were misread as HH:MM this would be tiny.
    assert t.turns[-1].start_seconds > 3000


def test_both_formats_produce_comparable_timestamps():
    """The two formats must land on the same scale, not merely both parse."""
    mm, hh = _load("casey-winters"), _load("brian-balfour")
    for t in (mm, hh):
        secs = [x.start_seconds for x in t.turns]
        assert secs == sorted(secs), f"{t.slug}: timestamps not monotonic"
        assert secs[0] == 0
        assert 1800 < secs[-1] < 20_000, f"{t.slug}: implausible final ts {secs[-1]}"


@pytest.mark.parametrize("ts,expected", [
    ("00:00", 0), ("01:39", 99), ("54:50", 3290),
    ("00:00:00", 0), ("01:28:43", 5323), ("00:54:50", 3290),
])
def test_parse_timestamp(ts, expected):
    assert parse_timestamp(ts) == expected


def test_parse_timestamp_rejects_garbage():
    with pytest.raises(ValidationFailed):
        parse_timestamp("12")


# --------------------------------------------------------------------------
# Case 10 -- malformed / zero-turn transcript must FAIL LOUDLY
# --------------------------------------------------------------------------
def test_zero_turn_transcript_raises_and_names_the_file():
    body = b"---\nguest: Nobody\n---\n\nJust prose with no speaker headers.\n"
    with pytest.raises(ValidationFailed) as e:
        parse_transcript("broken-episode", body)
    # Naming the file is the point: "0 turns" alone is not actionable.
    assert "broken-episode" in str(e.value)
    assert "ZERO" in str(e.value).upper()


def test_headers_with_empty_content_also_raise():
    body = b"---\nguest: X\n---\n\nAlice (00:01):\n\nBob (00:02):\n"
    with pytest.raises(ValidationFailed) as e:
        parse_transcript("hollow", body)
    assert "hollow" in str(e.value)


def test_wrong_timestamp_style_is_not_silently_accepted():
    """A file using an unsupported header shape must raise, not return []."""
    body = b"---\nguest: X\n---\n\n[00:01] Alice: hello there friend\n"
    with pytest.raises(ValidationFailed):
        parse_transcript("bracket-style", body)


# --------------------------------------------------------------------------
# Speaker normalisation -- N speakers, not host+guest
# --------------------------------------------------------------------------
def test_host_aliases_collapse():
    assert normalize_speaker("Lenny") == "Lenny Rachitsky"
    assert normalize_speaker("Lenny Rachitsky") == "Lenny Rachitsky"


def test_unknown_speakers_are_preserved_not_dropped():
    """4 of 20 curated episodes have a 3rd-5th speaker."""
    assert normalize_speaker("Christina Gilbert") == "Christina Gilbert"


def test_third_speaker_survives_parsing():
    t = _load("elena-verna")
    assert "Christina Gilbert" in t.speakers
    assert len(t.speakers) == 3


def test_casey_host_is_normalised_in_parsed_output():
    """casey-winters writes the host as bare 'Lenny'."""
    t = _load("casey-winters")
    assert "Lenny Rachitsky" in t.speakers
    assert "Lenny" not in t.speakers


# --------------------------------------------------------------------------
# Frontmatter -- and the field we deliberately refuse to store
# --------------------------------------------------------------------------
def test_duration_frontmatter_is_not_exposed():
    """casey claims duration 99s; its transcript runs to 3,290s.

    The field describes the linked YouTube clip. Exposing it would invite a
    wrong citation, so Transcript has no attribute for it at all.
    """
    t = _load("casey-winters")
    assert not hasattr(t, "duration")
    assert not hasattr(t, "duration_seconds")
    assert t.turns[-1].start_seconds > 3000  # the real extent


def test_multiline_title_is_joined():
    t = _load("casey-winters")
    assert "\n" not in t.title
    assert t.title.startswith("Why most product managers")


def test_keywords_parse_as_a_list():
    t = _load("brian-balfour")
    assert isinstance(t.keywords, list)
    assert "growth" in t.keywords


def test_content_hash_is_stable_and_of_raw_bytes():
    import hashlib
    raw = (EVIDENCE / "casey-winters.md").read_bytes()
    assert parse_transcript("casey-winters", raw).content_hash == \
        hashlib.sha256(raw).hexdigest()


# --------------------------------------------------------------------------
# Backchannel merging
# --------------------------------------------------------------------------
def test_backchannel_merges_forward_and_keeps_both_speakers():
    turns = [
        Turn("Lenny Rachitsky", 10, "Yeah."),
        Turn("Casey Winters", 12, " ".join(["substantive"] * 40)),
    ]
    out = merge_backchannel(turns)
    assert len(out) == 1
    # Attributed to whoever said the retrievable content...
    assert out[0].speaker == "Casey Winters"
    # ...but the other voice is not erased (skill 02: never lose attribution).
    assert "Lenny Rachitsky" in out[0].text
    assert "Yeah." in out[0].text
    # Timestamp rewinds to the start of the merged span, so a citation points
    # at the beginning of the exchange rather than mid-sentence.
    assert out[0].start_seconds == 10


def test_trailing_backchannel_is_not_dropped():
    turns = [
        Turn("A", 0, " ".join(["real"] * 40)),
        Turn("B", 90, "Totally."),
    ]
    out = merge_backchannel(turns)
    assert len(out) == 1
    assert "Totally." in out[0].text, "trailing backchannel silently discarded"


def test_merge_never_loses_words():
    t = _load("casey-winters")
    before = sum(x.word_count for x in t.turns)
    after = sum(x.word_count for x in merge_backchannel(t.turns))
    # Merging ADDS speaker-name tokens; it must never remove content.
    assert after >= before


def test_merge_reduces_turn_count_on_real_data():
    t = _load("casey-winters")
    merged = merge_backchannel(t.turns)
    assert len(merged) < t.turn_count
    assert all(x.word_count >= BACKCHANNEL_MAX_WORDS or len(merged) == 1
               for x in merged)


def test_merge_of_empty_input():
    assert merge_backchannel([]) == []
