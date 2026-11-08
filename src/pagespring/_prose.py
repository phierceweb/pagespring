"""A deliverable's prose: its text with code samples neutralized, so a ref scan sees only what renders
as an image. Markdown code is text whole; in a ``<pre>`` or ``<code>`` element only ``![](…)`` is."""

from __future__ import annotations

import re

from pagespring.patterns._md_code import prose_outside_code

# A quote opens a value only right after `=`; anywhere else it is text.
_ATTRS = r"""(?:[^<>=]|=\s*"[^"]*"|=\s*'[^']*'|=)*+"""
# Other tags are matched whole, so an opener inside one's attribute is not taken for code.
_TOKEN_RE = re.compile(
    rf"<(/?)(pre|code)(?=[\s/>]){_ATTRS}>|\n[ \t\r]*\n|</?[A-Za-z]{_ATTRS}>",
    re.IGNORECASE,
)


def _comments_masked(text: str) -> str:
    """``text`` with each comment spaced out at its own length: ``<!-->`` and ``<!--->`` are whole
    comments, and a ``<!--`` that never closes is text."""
    out: list[str] = []
    pos = 0
    while (start := text.find("<!--", pos)) >= 0:
        body = start + 4
        if text.startswith((">", "->"), body):
            end = text.index(">", body) + 1
        elif (close := text.find("-->", body)) >= 0:
            end = close + 3
        else:
            break
        out.append(text[pos:start])
        out.append(" " * (end - start))
        pos = end
    out.append(text[pos:])
    return "".join(out)


def _as_text(element: str) -> str:
    return element.replace("](", "] (")


def _code_elements(text: str, *, kind: str) -> list[tuple[int, int]]:
    """Spans of ``<pre>`` and ``<code>`` elements. An unclosed ``<pre>`` runs to the end; an unclosed
    ``<code>``, or in markdown one not closed within its paragraph, is not code."""
    spans: list[tuple[int, int]] = []
    opened: re.Match[str] | None = None
    for token in _TOKEN_RE.finditer(_comments_masked(text)):
        name = (token.group(2) or "").lower()
        if opened is None:
            if name and not token.group(1):
                opened = token
        elif token.group(1) and name == opened.group(2).lower():
            spans.append((opened.start(), token.end()))
            opened = None
        elif kind == "markdown" and token.group(0)[0] == "\n" and opened.group(2).lower() == "code":
            opened = None
    if opened is not None and opened.group(2).lower() == "pre":
        spans.append((opened.start(), len(text)))
    return spans


def _without_code_elements(text: str, *, kind: str) -> str:
    """Markdown targets inside each code element broken; a browser still renders an ``<img>``
    there."""
    out: list[str] = []
    pos = 0
    for start, end in _code_elements(text, kind=kind):
        out.append(text[pos:start])
        out.append(_as_text(text[start:end]))
        pos = end
    out.append(text[pos:])
    return "".join(out)


def prose(doc_text: str, *, kind: str) -> str:
    """``doc_text`` with its code samples made text: image syntax in ``<pre>`` and ``<code>`` elements
    in any kind, and in markdown also whole fenced and indented blocks and inline spans."""
    if kind == "markdown":
        doc_text = prose_outside_code(doc_text)
    return _without_code_elements(doc_text, kind=kind)
