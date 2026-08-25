"""The Phase 7 trust boundary: `app.artifacts.render`.

Skill 06's five required verification inputs, plus the specific attack
classes behind them. `render()` is a pure function -- no fixtures, no
database, no event loop -- so every case here is a direct call.

Two invariants run as properties across every case rather than as one-off
assertions: the output never contains a live script/handler/dangerous-scheme
construct (`_assert_no_live_markup`), and citation tags / quotation marks
survive rendering (`_assert_preserves_citation_content`) -- because a
sanitizer that quietly ate `[E#]` or a quote would break the audit trail
grounding depends on, which is a worse failure than not sanitizing at all.
"""
from __future__ import annotations

import html as html_module
import re

import pytest

from app.artifacts import BLOCKED_TAGS, POLICY_VERSION, render
from app.errors import (
    ArtifactRenderFailed,
    ArtifactTooLarge,
    ArtifactUnsafe,
    ArtifactUnsupportedFormat,
)

# The same structural check `render()` runs on itself post-sanitize. Reused
# here as the test suite's own independent assertion, not just a call into
# the code under test.
_LIVE_MARKUP_RE = re.compile(
    r"<script|<iframe|<style|<svg|srcdoc\s*=|on\w+\s*=\s*[\"']|"
    r"(?:href|src|action|formaction)\s*=\s*[\"']\s*(?:javascript|vbscript|data):",
    re.IGNORECASE,
)


def _assert_no_live_markup(html: str) -> None:
    assert not _LIVE_MARKUP_RE.search(html), f"live markup survived: {html!r}"


def _assert_preserves_citation_content(html: str, *, tags: list[str],
                                       quotes: list[str]) -> None:
    """Decoded text content, not raw bytes: markdown-it-py escapes `"` to
    `&quot;` in text nodes (matching the CommonMark reference renderer),
    which is a lossless encoding -- a browser renders `&quot;` as `"`. The
    guarantee that matters is what a reader sees, so entity-decode before
    comparing.
    """
    decoded = html_module.unescape(html)
    for tag in tags:
        assert tag in decoded, f"citation tag {tag!r} lost: {html!r}"
    for quote in quotes:
        assert quote in decoded, f"quoted span {quote!r} lost: {html!r}"


# ---------------------------------------------------------------------------
# 1. Benign Markdown artifact
# ---------------------------------------------------------------------------

def test_benign_markdown_renders_expected_structure():
    src = (
        "# A Title\n\n"
        "Some **bold** and *em* text with a [E1] citation.\n\n"
        "- one\n- two\n\n"
        "> a blockquote\n"
    )
    result = render(src, format="markdown")
    assert "<h1>A Title</h1>" in result.html
    assert "<strong>bold</strong>" in result.html
    assert "<em>em</em>" in result.html
    assert "<li>one</li>" in result.html
    assert "<blockquote>" in result.html
    assert result.policy_version == POLICY_VERSION
    assert result.blocked == 0
    assert result.stripped == 0
    _assert_no_live_markup(result.html)


def test_a_realistic_essay_preserves_every_citation_tag_and_quote():
    """The property test against essay-shaped content: multiple headings,
    bold quoted spans, and several [E#] tags across paragraphs -- modeled on
    a real generated Ship 30 essay's structure (docs/ship30-essays.md).
    """
    src = (
        '# Distribution Is the Game\n\n'
        'Brian Balfour puts it directly: building a great product is '
        '**"necessary, but not sufficient"**; the real separation is '
        'distribution [E1].\n\n'
        '## The escape velocity problem\n\n'
        'He cites the view that the AI shift **"has not yet come with a '
        'distribution shift"** [E1]. Facebook was in a **"brutal battle"** '
        'with MySpace and Friendster [E2].\n\n'
        '## Growth teams do not fix bad foundations\n\n'
        'The evidence is clear that tactics cannot rescue a broken model '
        '[E3].\n'
    )
    result = render(src, format="markdown")
    _assert_no_live_markup(result.html)
    _assert_preserves_citation_content(
        result.html,
        tags=["[E1]", "[E2]", "[E3]"],
        quotes=[
            "necessary, but not sufficient",
            "has not yet come with a distribution shift",
            "brutal battle",
        ],
    )


# ---------------------------------------------------------------------------
# 2. Benign HTML/CSS artifact
# ---------------------------------------------------------------------------

def test_benign_html_snippet_renders_permitted_tags():
    src = (
        "<h2>Section</h2>"
        "<p>Text with <strong>emphasis</strong> and <code>a_var</code>.</p>"
        "<ul><li>one</li><li>two</li></ul>"
    )
    result = render(src, format="html")
    assert "<h2>Section</h2>" in result.html
    assert "<strong>emphasis</strong>" in result.html
    assert "<code>a_var</code>" in result.html
    assert result.blocked == 0
    assert result.stripped == 0
    _assert_no_live_markup(result.html)


# ---------------------------------------------------------------------------
# 3. Script-bearing artifact (M17) -- both formats, several vectors
# ---------------------------------------------------------------------------

def test_raw_script_tag_in_html_format_is_removed_content_and_all():
    """Where nh3 is the ONLY gate (raw HTML/CSS input), a blocked tag's
    content is removed along with it, per the stated policy.
    """
    src = '<p>before</p><script>alert(document.cookie)</script><p>after</p>'
    result = render(src, format="html")
    assert "alert(document.cookie)" not in result.html
    _assert_no_live_markup(result.html)


def test_raw_script_in_markdown_source_becomes_inert_visible_text():
    """Where `html=False` is the first gate (Markdown input, the real
    producer path), a `<script>` in the SOURCE is neutralized to literal
    text before nh3 ever runs -- it is never a tag to strip, so its text
    survives, visibly, as harmless prose rather than as executing markup.
    That distinction is exactly what the docstring in app/artifacts.py
    documents: "blocked" for the HTML-input path is a stronger, structural
    guarantee for the Markdown path -- the payload can never become a tag
    at all, not merely a stripped one.
    """
    src = '<p>before</p><script>alert(document.cookie)</script><p>after</p>'
    result = render(src, format="markdown")
    _assert_no_live_markup(result.html)
    assert "&lt;script&gt;" in result.html


def test_script_via_svg_is_removed_content_and_all():
    src = '<svg onload=alert(1)><script>alert(2)</script></svg>'
    result = render(src, format="html")
    assert "alert(1)" not in result.html
    assert "alert(2)" not in result.html
    _assert_no_live_markup(result.html)


@pytest.mark.parametrize("payload", [
    '<img src=x onerror=alert(1)>',
    '<p onclick="alert(1)">click</p>',
    '<div onmouseover="alert(1)">hover</div>',
])
def test_event_handler_attributes_are_stripped(payload):
    result = render(payload, format="html")
    _assert_no_live_markup(result.html)
    assert "alert(1)" not in result.html or "onerror" not in result.html


@pytest.mark.parametrize("fmt", ["markdown", "html"])
def test_double_encoded_script_stays_inert(fmt):
    """A source that has already HTML-entity-encoded a script tag must not
    become live markup when decoded a second time by a browser. nh3/the
    markdown renderer must not double-DECODE it back into structure.
    """
    src = "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>"
    result = render(src, format=fmt)
    _assert_no_live_markup(result.html)


def test_javascript_scheme_link_is_neutralized_not_refused():
    """A dangerous scheme in a real markdown link must render SAFELY, not
    trip the post-render re-scan -- the whole point of unwrapping a link to
    inert parenthetical text is that the URL is now just text, and text
    mentioning "javascript:" is not itself a live construct.
    """
    src = "[click me](javascript:alert(1))"
    result = render(src, format="markdown")
    _assert_no_live_markup(result.html)


def test_javascript_scheme_href_in_raw_html_is_stripped():
    src = '<a href="javascript:alert(1)">click</a>'
    result = render(src, format="html")
    _assert_no_live_markup(result.html)
    assert "click" in result.html  # unwrapped, kept as text
    assert "javascript:" not in result.html


# ---------------------------------------------------------------------------
# 4. External resource references
# ---------------------------------------------------------------------------

def test_markdown_image_is_replaced_by_a_visible_marker_and_counted():
    src = "![beacon](https://evil.example.com/track.png)"
    result = render(src, format="markdown")
    assert "[image removed]" in result.html
    assert "evil.example.com" not in result.html
    assert result.stripped == 1


def test_markdown_link_is_unwrapped_to_text_plus_url_in_parens():
    """The URL becomes inert, visible text -- never a clickable, model-
    authored citation. The only clickable citations in the pane are the
    retrieval-derived ones Citations.tsx renders, outside this HTML.
    """
    src = "[the source](https://example.com/article)"
    result = render(src, format="markdown")
    assert "<a" not in result.html
    assert "the source" in result.html
    assert "(https://example.com/article)" in result.html
    assert result.stripped == 1


def test_html_external_image_and_link_are_stripped():
    src = (
        '<img src="https://evil.example.com/beacon.png">'
        '<link rel="stylesheet" href="https://evil.example.com/x.css">'
    )
    result = render(src, format="html")
    assert "evil.example.com" not in result.html
    _assert_no_live_markup(result.html)


# ---------------------------------------------------------------------------
# 5. Malformed / truncated artifact
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src", [
    "# Unterminated **bold and *em\n\n[E1] some `unterminated code",
    "<div><p>unterminated<div><span>deeply nested and never closed",
    "",
    "   \n\n   ",
    "# " + ("x" * 5000),
])
def test_malformed_or_truncated_input_does_not_raise(src):
    result = render(src, format="markdown")
    _assert_no_live_markup(result.html)


def test_oversized_source_is_refused_before_parsing():
    with pytest.raises(ArtifactTooLarge) as exc_info:
        render("x" * (300 * 1024), format="markdown")
    assert exc_info.value.code == "artifact_too_large"
    assert exc_info.value.http_status == 413


def test_unsupported_format_is_refused():
    with pytest.raises(ArtifactUnsupportedFormat) as exc_info:
        render("hello", format="pdf")
    assert exc_info.value.code == "artifact_unsupported_format"


# ---------------------------------------------------------------------------
# Comments, entities and every hard-blocked tag
# ---------------------------------------------------------------------------

def test_html_comments_are_stripped():
    src = "<p>visible</p><!-- a secret comment --><p>also visible</p>"
    result = render(src, format="html")
    assert "a secret comment" not in result.html


# HTML5 void elements: a parser never gives these children, so text placed
# "inside" a hand-written `<input>payload</input>` is actually a SIBLING of
# the (empty) tag, not its content -- nh3 correctly drops the tag alone.
# Content removal is exercised separately, for real containers, below.
_VOID_BLOCKED_TAGS = frozenset({"input", "link", "meta", "base", "embed"})


@pytest.mark.parametrize("tag", sorted(BLOCKED_TAGS))
def test_every_blocked_tag_is_stripped_in_html_format(tag):
    src = f"<{tag}>payload</{tag}>"
    result = render(src, format="html")
    assert f"<{tag}" not in result.html.lower()
    _assert_no_live_markup(result.html)
    if tag not in _VOID_BLOCKED_TAGS:
        assert "payload" not in result.html


def test_blocked_tags_are_counted_in_html_format():
    src = "<script>a</script><style>b</style><iframe></iframe>"
    result = render(src, format="html")
    assert result.blocked == 3


# ---------------------------------------------------------------------------
# The sanitizer's own failure mode is fail-closed, never a partial render
# ---------------------------------------------------------------------------

def test_sanitizer_failure_raises_render_failed(monkeypatch):
    import app.artifacts as artifacts_mod

    def _boom(html, **kwargs):
        raise RuntimeError("sanitizer exploded")

    monkeypatch.setattr(artifacts_mod.nh3, "clean", _boom)
    with pytest.raises(ArtifactRenderFailed):
        artifacts_mod.render("# hi", format="markdown")


def test_post_render_unsafe_content_is_refused(monkeypatch):
    """If nh3's allowlist ever regressed and let something live through, the
    re-scan must still catch it. Simulated by monkeypatching nh3.clean to
    return unsanitized input -- the one path that must fail closed even when
    the primary sanitizer fails open.
    """
    import app.artifacts as artifacts_mod

    monkeypatch.setattr(artifacts_mod.nh3, "clean", lambda html, **kw: html)
    with pytest.raises(ArtifactUnsafe):
        artifacts_mod.render(
            '<script>alert(1)</script>', format="html")


class TestFrontendNeverParsesUntrustedMarkup:
    """The client half of the trust boundary, asserted against the SOURCE.

    Frontend source is not in the runtime/test image (the Dockerfile COPYs
    only the built `dist/`, per the `frontend` build stage) -- same reason
    `test_ship30.py`'s `.dockerignore`/`Dockerfile` packaging tests skip
    in-container. This mirrors that pattern rather than inventing a new one.

    Phase 6 asserted "0 dangerouslySetInnerHTML, 0 iframe, 0 innerHTML" as a
    manually-verified property. Phase 7 changes it deliberately -- one
    `iframe` now exists -- so this locks in the NEW, narrower invariant as an
    automated regression gate: the app document still never parses untrusted
    markup, and the one iframe that exists is sandboxed correctly.
    """

    @staticmethod
    def _strip_comments(text: str) -> str:
        """Block and line comments removed, so a docstring that EXPLAINS the
        invariant (and necessarily mentions the very strings being checked
        for, including an illustrative `<iframe sandbox="">` example) cannot
        false-positive a check for real, executable usage. Not a full
        tokenizer -- adequate for well-formed TSX with no string literal
        containing a literal `/*` or `//`, which is true of this codebase.
        """
        import re as _re
        text = _re.sub(r"/\*.*?\*/", "", text, flags=_re.DOTALL)
        text = _re.sub(r"//[^\n]*", "", text)
        return text

    @classmethod
    def _frontend_file(cls, rel: str) -> str:
        from pathlib import Path
        path = Path(__file__).parents[2] / "frontend" / "src" / rel
        if not path.is_file():
            pytest.skip(
                f"frontend/src/{rel} is not present -- this is the container "
                f"image, which ships only the built dist/. Run on the host.")
        return cls._strip_comments(path.read_text(encoding="utf-8"))

    def test_no_dangerously_set_inner_html_anywhere_in_the_app(self):
        import re as _re
        from pathlib import Path
        src_dir = Path(__file__).parents[2] / "frontend" / "src"
        if not src_dir.is_dir():
            pytest.skip("frontend/src is not present in this image.")
        offenders = []
        for f in src_dir.rglob("*.ts*"):
            code = self._strip_comments(f.read_text(encoding="utf-8"))
            if "dangerouslySetInnerHTML" in code or _re.search(
                    r"\.innerHTML\s*=", code):
                offenders.append(str(f))
        assert offenders == [], (
            f"untrusted-markup escape hatch found outside app/artifacts.py: {offenders}")

    def test_exactly_one_sandboxed_iframe_and_it_withholds_scripts_and_same_origin(self):
        import re as _re
        code = self._frontend_file("components/ArtifactPane.tsx")
        assert code.count("<iframe") == 1, (
            "exactly one iframe is the Phase 7 isolation boundary -- "
            "more than one means a second, unaudited surface exists")

        # Scoped to the tag's own attribute list -- the empty-state help
        # text legitimately mentions "allow-scripts" and "allow-same-origin"
        # in <code> as PROSE explaining the policy to the user; that is not
        # a permission being granted and must not fail this check.
        tag = _re.search(r"<iframe\b(.*?)/?>", code, _re.DOTALL)
        assert tag is not None, "iframe tag not found"
        attrs = tag.group(1)
        assert 'sandbox=""' in attrs, (
            "the sandbox attribute must be present and EMPTY -- any value "
            "at all is a permission being granted")
        assert "allow-scripts" not in attrs
        assert "allow-same-origin" not in attrs

    def test_srcdoc_is_fed_only_from_the_render_endpoint_response(self):
        """The iframe's content must trace back to the sanitized server
        response (`essay.render...html`), never to `essay.markdown` (the raw,
        untrusted source) or any other unsanitized string.
        """
        text = self._frontend_file("components/ArtifactPane.tsx")
        srcdoc_line = next(
            (ln for ln in text.splitlines() if "srcDoc=" in ln), None)
        assert srcdoc_line is not None, "no srcDoc prop found on the iframe"
        assert "essay.markdown" not in srcdoc_line

    def test_essay_markdown_source_view_still_renders_as_a_react_text_child(self):
        """Phase 6's guarantee, unchanged: the Source toggle shows the raw
        Markdown as an escaped React text node, never parsed.
        """
        text = self._frontend_file("components/ArtifactPane.tsx")
        assert "{essay.markdown}" in text
