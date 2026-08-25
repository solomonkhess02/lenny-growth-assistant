"""Artifact rendering -- the Phase 7 trust boundary.

Skill 06: the model, the conversation and the transcripts are all untrusted
authors. An essay's Markdown is shaped by all three, so nothing about it is
trusted before it reaches here -- not headings, not a citation tag, not a
"safe-looking" link.

This module is the ONE place untrusted text becomes HTML. It is a pure
function: no I/O, no database, no network, so it is exhaustively testable and
the isolation policy documented in `docs/artifact-isolation.md` (decision
D-4) is fully expressed as code, not prose split across call sites.

Two independent layers, not one:

  1. `markdown-it-py` runs with `html=False`. Raw HTML in the SOURCE (a
     transcript quoting `<script>` verbatim, a model emitting markup) is
     never parsed as markup -- it is escaped to literal text at the parser
     level, structurally, before nh3 ever runs. This is why the "blocked"
     tag list in the policy doc matters most for the `format="html"` path:
     the markdown path already can't produce those tags from source HTML.
  2. `nh3` (the Rust `ammonia` sanitizer) cleans whatever HTML the renderer
     DID legitimately produce -- headings, emphasis, and the neutralized
     link/image text below -- against an explicit allowlist. It is the
     second, independent gate: a bug in (1) is still caught by (2).

Links and images get a third treatment, neither "permit" nor "block": a
markdown link `[text](url)` legitimately renders through `nh3` if `a` stays
allowlisted, but a model-authored URL is not a verified citation -- the only
clickable citations in the pane are the retrieval-derived ones in
`Citations.tsx`, rendered outside this HTML entirely. So `link_open`/
`link_close`/`image` are overridden at the markdown-it-py renderer level to
never emit a real `<a>`/`<img>` at all: a link becomes its text followed by
the URL in parentheses as inert text, and an image becomes a visible
`[image removed]` marker. `nh3` then never sees an `a` or `img` tag to make a
decision about -- there isn't one to allow or strip.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import nh3
from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml

from .errors import (
    ArtifactRenderFailed,
    ArtifactTooLarge,
    ArtifactUnsafe,
    ArtifactUnsupportedFormat,
)

log = logging.getLogger("app.artifacts")

POLICY_VERSION = "1"

# A real essay is ~1,250 words, well under 10 KiB. 256 KiB is generous
# headroom for a pathological input, not a realistic essay size.
MAX_SOURCE_BYTES = 256 * 1024

# --- the policy, as an allowlist -------------------------------------------
#
# Everything a Ship 30 essay legitimately needs, and nothing else. No
# attributes survive on any tag: no `style`, `id`, `class` from the source,
# because an attribute is exactly where a payload hides once the tag itself
# is permitted.
#
# No `table`: the commonmark preset this module runs (deliberately, so
# nothing beyond the core spec is in scope) has no table extension enabled,
# so the parser can never emit one from the markdown path. Declaring it
# permitted would be a claim about a code path that does not exist.
PERMITTED_TAGS = frozenset({
    "h1", "h2", "h3", "h4", "p", "ul", "ol", "li",
    "strong", "em", "b", "i", "blockquote", "code", "pre", "hr", "br",
})

# Named for the policy doc and for blocked-element counting on the raw-HTML
# input path (see `_count_tags` below). Every one of these is excluded from
# PERMITTED_TAGS, so nh3 strips the tag -- and every one is also in
# CLEAN_CONTENT_TAGS below, so its content goes with it. A stray `payload`
# inside a stripped `<form>` or `<iframe>` is inert either way (text does not
# execute), but the stated policy is "element and contents removed" for the
# whole blocked list, not just script/style, so the code matches that
# literally rather than drawing its own narrower line.
BLOCKED_TAGS = frozenset({
    "script", "style", "iframe", "object", "embed", "applet", "form",
    "input", "button", "link", "meta", "base", "svg", "math", "template",
    "noscript",
})
CLEAN_CONTENT_TAGS = BLOCKED_TAGS

# Belt-and-braces re-scan of our OWN sanitizer output. Expected to never
# match -- if it does, nh3's allowlist has a gap, and failing closed here is
# the whole point of defence in depth.
#
# Deliberately structural, not a bare substring check: a link to a
# `javascript:` URL is POLICY-COMPLIANT output once neutralized (the URL
# survives only as inert parenthetical text, e.g. "click me
# (javascript:alert(1))"), and a naive `"javascript:" in html` check would
# refuse that safe, correct result. This pattern instead matches only where
# the string would be live: a real tag, an event-handler attribute, or a
# dangerous scheme actually sitting in a URL attribute's value.
_UNSAFE_RE = re.compile(
    r"<script|<iframe|<style|<svg|srcdoc\s*=|on\w+\s*=\s*[\"']|"
    r"(?:href|src|action|formaction)\s*=\s*[\"']\s*(?:javascript|vbscript|data):",
    re.IGNORECASE,
)

_TAG_OPEN_RE_CACHE: dict[frozenset[str], re.Pattern[str]] = {}


def _count_tags(html: str, names: frozenset[str]) -> int:
    """How many opening tags in `names` occur in `html`.

    Used for provenance counts, not for policy enforcement -- nh3 enforces
    the policy regardless of what this counts. A cheap regex is fine because
    it only has to be right about counting, on our own already-generated
    or caller-supplied HTML, not about parsing untrusted structure safely.
    """
    pattern = _TAG_OPEN_RE_CACHE.get(names)
    if pattern is None:
        alternation = "|".join(re.escape(n) for n in sorted(names))
        pattern = re.compile(rf"<(?:{alternation})\b", re.IGNORECASE)
        _TAG_OPEN_RE_CACHE[names] = pattern
    return len(pattern.findall(html))


def _link_open(self, tokens, idx, options, env) -> str:  # noqa: ANN001
    # Suppress the opening tag; the link's own text still renders normally
    # through the surrounding inline rules.
    return ""


def _link_close(self, tokens, idx, options, env) -> str:  # noqa: ANN001
    href = ""
    for j in range(idx - 1, -1, -1):
        if tokens[j].type == "link_open":
            href = tokens[j].attrGet("href") or ""
            break
    if not href:
        return ""
    return f" ({escapeHtml(href)})"


def _image(self, tokens, idx, options, env) -> str:  # noqa: ANN001
    return "[image removed]"


def _new_markdown_renderer() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False})
    md.add_render_rule("link_open", _link_open)
    md.add_render_rule("link_close", _link_close)
    md.add_render_rule("image", _image)
    return md


@dataclass(frozen=True)
class RenderResult:
    html: str
    blocked: int
    stripped: int
    policy_version: str = POLICY_VERSION


def _markdown_to_html(source: str) -> tuple[str, int]:
    """Render Markdown to HTML, counting neutralized links/images.

    Raw HTML in `source` never becomes a real tag here -- `html=False` at
    construction escapes it to text at parse time, before this function
    or nh3 ever sees it as structure.
    """
    stripped = 0

    def counting_link_open(self, tokens, idx, options, env):  # noqa: ANN001
        nonlocal stripped
        stripped += 1
        return _link_open(self, tokens, idx, options, env)

    def counting_image(self, tokens, idx, options, env):  # noqa: ANN001
        nonlocal stripped
        stripped += 1
        return _image(self, tokens, idx, options, env)

    # A fresh renderer per call: the `stripped` counter above is captured by
    # closure into `counting_link_open`/`counting_image`, so a shared
    # module-level renderer would leak one call's count into the next.
    md = _new_markdown_renderer()
    md.add_render_rule("link_open", counting_link_open)
    md.add_render_rule("image", counting_image)
    html = md.render(source)
    return html, stripped


def render(source: str, *, format: str = "markdown") -> RenderResult:  # noqa: A002
    """Render untrusted essay text to sanitized, isolatable HTML.

    Raises an `AppError` subclass (never returns a partial or "mostly safe"
    result) when the input cannot be rendered under the stated policy --
    fail-closed is the contract, not an edge case of it. The caller falls
    back to the escaped-source view already shown since Phase 6.
    """
    if format not in ("markdown", "html"):
        raise ArtifactUnsupportedFormat(
            f"Artifact format {format!r} is not supported.",
            format=format)

    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ArtifactTooLarge(
            "The artifact exceeds the size limit for rendering.",
            size_bytes=len(source.encode("utf-8")), limit_bytes=MAX_SOURCE_BYTES)

    try:
        if format == "markdown":
            html, stripped = _markdown_to_html(source)
            blocked = _count_tags(html, BLOCKED_TAGS)
        else:
            # Raw HTML/CSS input: there is no markdown-it pass to neutralize
            # links/images ahead of time, so count them here, before nh3
            # strips the tags (nh3 does not report what it removed).
            stripped = _count_tags(source, frozenset({"a", "img"}))
            blocked = _count_tags(source, BLOCKED_TAGS)
            html = source

        cleaned = nh3.clean(
            html,
            tags=PERMITTED_TAGS,
            clean_content_tags=CLEAN_CONTENT_TAGS,
            attributes={},
            strip_comments=True,
            link_rel=None,
            url_schemes=set(),
        )
    except (ArtifactTooLarge, ArtifactUnsupportedFormat):
        raise
    except Exception as exc:
        log.exception("artifact_render_failed", extra={"format": format})
        raise ArtifactRenderFailed(
            "The artifact could not be rendered.", format=format) from exc

    if _UNSAFE_RE.search(cleaned):
        log.error("artifact_unsafe_after_sanitize", extra={
            "format": format, "policy_version": POLICY_VERSION})
        raise ArtifactUnsafe(
            "The artifact failed a post-render safety check.",
            format=format)

    return RenderResult(
        html=cleaned, blocked=blocked, stripped=stripped,
        policy_version=POLICY_VERSION)
