"""Sanitizers that keep untrusted text inside its prompt boundary.

Web search results are attacker-controllable. Before they go into the
generator prompt they are cleaned so they cannot:
  * open or close prompt boundary tags (all '<' / '>' removed),
  * forge extra numbered sources or citations ('[n]' markers removed),
  * plant markdown images that the browser would fetch (exfiltration).
"""

from __future__ import annotations

import re

# Inline images: ![alt](url "title"), allowing one level of nested parens in the URL.
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((?:[^()]|\([^()]*\))*\)")
# Anything left that still starts image syntax (reference-style ![alt][ref], unterminated, ...).
_MD_IMAGE_OPENER_RE = re.compile(r"!\s*\[")
# Citation-like markers: [2], [ 12 ].
_CITATION_MARKER_RE = re.compile(r"\[\s*\d{1,3}\s*\]")
_TAG_CHARS_RE = re.compile(r"[<>]")
# Our own boundary tags, in any case or spacing, e.g. </external_web_source >.
_BOUNDARY_TAG_RE = re.compile(
    r"<\s*/?\s*(?:user_question|search_query|sources|external_web_source|internal_doc)\b[^>]*>", re.IGNORECASE
)
_ATTR_UNSAFE_RE = re.compile(r"[\s<>\"'`]")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def strip_markdown_images(text: str) -> str:
    """Remove markdown images so rendering the text never triggers a remote fetch."""
    text = _MD_IMAGE_RE.sub("", text)
    return _MD_IMAGE_OPENER_RE.sub("[", text)


def strip_boundary_tags(text: str) -> str:
    """Remove only the prompt's own boundary tags (for trusted text that may contain normal '<'...)."""
    return _BOUNDARY_TAG_RE.sub("", text)


def clean_web_text(text: str) -> str:
    """Sanitize an untrusted web snippet before it is placed in the generator prompt."""
    text = strip_markdown_images(text)
    text = _TAG_CHARS_RE.sub("", text)
    text = _CITATION_MARKER_RE.sub("", text)
    return _BLANK_LINES_RE.sub("\n\n", text).strip()


def clean_attribute(value: str, max_len: int = 300) -> str:
    """Make a value safe to place inside a double-quoted tag attribute."""
    return _ATTR_UNSAFE_RE.sub("", value)[:max_len]
