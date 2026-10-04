"""MathJax SVG output rebuilt as MathML: each group names its element (``data-mml-node``) and each
glyph its code point (``data-c``), since a text extraction of the paths is empty."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterator

from bs4 import BeautifulSoup
from bs4.element import Tag

_TOKENS = frozenset({"mi", "mn", "mo", "mtext", "ms"})
# MathML presentation elements; any other name a group carries is page data.
_ELEMENTS = _TOKENS | frozenset(
    {
        "math",
        "mrow",
        "mfrac",
        "msqrt",
        "mroot",
        "mstyle",
        "merror",
        "mpadded",
        "mphantom",
        "menclose",
        "mspace",
        "msub",
        "msup",
        "msubsup",
        "munder",
        "mover",
        "munderover",
        "mmultiscripts",
        "mprescripts",
        "none",
        "mtable",
        "mtr",
        "mtd",
        "mlabeledtr",
        "semantics",
        "annotation",
    }
)
_RADICALS = frozenset({"msqrt", "mroot"})


def rebuild_math(root: Tag) -> None:
    """Replace each MathJax container under ``root`` with its MathML."""
    for box in root.find_all("mjx-container"):
        if box.decomposed:
            continue
        published = box.find("math")
        if isinstance(published, Tag):
            box.replace_with(published.extract())
            continue
        top = box.select_one("[data-mml-node=math]")
        if not isinstance(top, Tag):
            continue
        math = _element(BeautifulSoup("", "html.parser"), top)
        if box.get("display") == "true":
            math["display"] = "block"
        box.replace_with(math)


def _element(soup: BeautifulSoup, group: Tag) -> Tag:
    name = str(group["data-mml-node"])
    el = soup.new_tag(name if name in _ELEMENTS else "mrow")
    if el.name in _TOKENS:
        el.string = _glyphs(group)
        return el
    for child in _child_groups(group):
        # MathJax draws a radical's surd as an operator; MathML's radical implies it.
        if el.name in _RADICALS and child["data-mml-node"] == "mo" and _glyphs(child) == "√":
            continue
        el.append(_element(soup, child))
    if el.name == "mtable":
        _seat_labels(el)
    return el


def _seat_labels(table: Tag) -> None:
    """Lead each labelled row with its label, which MathJax draws after the rows."""
    labels = table.find_all("mtd", recursive=False)
    rows = table.find_all("mlabeledtr", recursive=False)
    if len(labels) == len(rows):
        for row, label in zip(rows, labels, strict=True):
            row.insert(0, label.extract())


def _child_groups(group: Tag) -> Iterator[Tag]:
    """The nearest descendants naming a MathML element, in document order."""
    for child in group.children:
        if isinstance(child, Tag):
            if child.has_attr("data-mml-node"):
                yield child
            else:
                yield from _child_groups(child)


def _glyphs(token: Tag) -> str:
    """A token's text; NFKC folds MathJax's Mathematical Italic back to plain letters."""
    out: list[str] = []
    for el in token.find_all(True):
        code = el.get("data-c")
        if el.name == "text":
            out.append(el.get_text())
        elif isinstance(code, str):
            try:
                out.append(chr(int(code, 16)))
            except ValueError:
                continue
    return unicodedata.normalize("NFKC", "".join(out))
