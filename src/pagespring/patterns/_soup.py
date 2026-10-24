"""Parse HTML the same on every Python: 3.12's ``html.parser`` decodes ``&reg=`` in an attribute to
``®=``, and 3.13+ leaves it as written, as the HTML spec does. Parse through ``parse_html``."""

from __future__ import annotations

import re
from html.entities import html5

from bs4 import BeautifulSoup

# html.parser's own tokenizer in current 3.12-3.14 releases, so this reads the tags the parser reads.
_TAG_END_RE = re.compile(
    r"""[a-zA-Z][^\t\n\r\f />]*[\t\n\r\f /]*
    (?:(?<=['"\t\n\r\f /])[^\t\n\r\f />][^\t\n\r\f /=>]*
      (?:[\t\n\r\f ]*=[\t\n\r\f ]*(?:'[^']*'|"[^"]*"|(?!['"])[^>\t\n\r\f ]*))?
      [\t\n\r\f /]*
    )*>?""",
    re.VERBOSE,
)
_TAG_NAME_RE = re.compile(r"([a-zA-Z][^\t\n\r\f />]*)(?:[\t\n\r\f ]|/(?!>))*")
_ATTR_RE = re.compile(
    r"""((?<=['"\t\n\r\f /])[^\t\n\r\f />][^\t\n\r\f /=>]*)
    ([\t\n\r\f ]*=[\t\n\r\f ]*('[^']*'|"[^"]*"|(?!['"])[^>\t\n\r\f ]*))?
    (?:[\t\n\r\f ]|/(?!>))*""",
    re.VERBOSE,
)
_LETTER_RE = re.compile(r"[a-zA-Z]")
_COMMENT_END_RE = re.compile(r"--!?>")
_COMMENT_ABRUPT_END_RE = re.compile(r"-?>")
# Elements the parser reads as text up to their end tag; ``plaintext`` reads to the end.
_RAW_TEXT = {"script", "style", "xmp", "iframe", "noembed", "noframes", "textarea", "title"}
_ATTR_REF_RE = re.compile(r"&(#[0-9]+|#[xX][0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*)[;=]?")


def _hold(match: re.Match[str]) -> str:
    ref = match.group()
    # The spec decodes a numeric ref, or a whole entity name that no ``=`` follows.
    if ref[1] == "#" or (not ref.endswith("=") and ref[1:] in html5):
        return ref
    return "&amp;" + ref[1:]


def _past(markup: str, at: int) -> int:
    """Where the parser resumes after the ``<`` at ``at`` that opens no start tag."""
    n = len(markup)
    if markup.startswith("<!--", at):
        close = _COMMENT_END_RE.search(markup, at + 4) or _COMMENT_ABRUPT_END_RE.match(
            markup, at + 4
        )
        return close.end() if close else n
    if markup.startswith("</", at) and _LETTER_RE.match(markup, at + 2):
        end = _TAG_END_RE.match(markup, at + 2)
        return end.end() if end and markup[end.end() - 1] == ">" else n
    if markup.startswith("<![CDATA[", at):
        close_at = markup.find("]]>", at + 9)
        return close_at + 3 if close_at >= 0 else n
    if markup.startswith(("</", "<!", "<?"), at):
        close_at = markup.find(">", at + 2)
        return close_at + 1 if close_at >= 0 else n
    return at + 1


def _start_tag(markup: str, at: int) -> tuple[int, str | None, str]:
    """Where the parser resumes after the start tag at ``at``, the tag with its values' refs held
    (None when the parser drops it or reads it as text), and the element it opens."""
    found = _TAG_END_RE.match(markup, at + 1)
    name = _TAG_NAME_RE.match(markup, at + 1)
    if found is None or name is None or markup[found.end() - 1] != ">":
        return len(markup), None, ""
    end = found.end()
    pieces, done, k = [], at, name.end()
    while k < end and (attr := _ATTR_RE.match(markup, k)) is not None:
        if attr[3]:
            start, stop = attr.span(3)
            pieces += [markup[done:start], _ATTR_REF_RE.sub(_hold, markup[start:stop])]
            done = stop
        k = attr.end()
    tail = markup[k:end].strip()
    if tail not in (">", "/>"):
        return end, None, ""
    return end, "".join([*pieces, markup[done:end]]), name[1].lower() if tail == ">" else ""


def hold_attribute_refs(markup: str) -> str:
    """``markup`` with each ``&`` the spec keeps in an attribute value written ``&amp;``, so every
    parser reads the value as written; text, comments and raw-text elements are left alone."""
    if "&" not in markup:
        return markup
    pieces: list[str] = []
    done = pos = 0
    while (at := markup.find("<", pos)) >= 0:
        if not _LETTER_RE.match(markup, at + 1):
            pos = _past(markup, at)
            continue
        pos, tag, opens = _start_tag(markup, at)
        if tag is None:
            continue
        pieces += [markup[done:at], tag]
        done = pos
        if opens == "plaintext":
            break
        if opens in _RAW_TEXT:
            close = re.compile(rf"</{opens}(?=[\t\n\r\f />])", re.IGNORECASE | re.ASCII)
            found = close.search(markup, pos)
            pos = found.start() if found else len(markup)
    return "".join([*pieces, markup[done:]])


def parse_html(markup: str) -> BeautifulSoup:
    """``markup`` parsed with ``html.parser``, attribute values read as the HTML spec reads them."""
    return BeautifulSoup(hold_attribute_refs(markup), "html.parser")
