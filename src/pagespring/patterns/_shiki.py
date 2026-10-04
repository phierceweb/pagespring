"""Shiki code blocks reduced to plain ``<pre><code>``: its per-token styled spans outweigh the code
several times over."""

from __future__ import annotations

from bs4 import BeautifulSoup
from bs4.element import Tag

_DIFF_MARKS = {"add": "+", "remove": "-"}
_POPUP_CSS = ".twoslash-popup-container"


def _is_shiki(pre: Tag) -> bool:
    return (
        "shiki" in pre.get_attribute_list("class")
        or pre.select_one('span[style*="--shiki"]') is not None
    )


def _language(pre: Tag, code: Tag) -> str | None:
    """A ``language-*`` class or ``data-language`` on the code, the pre, or its wrapper."""
    for node in (code, pre, pre.parent):
        if not isinstance(node, Tag):
            continue
        for cls in node.get_attribute_list("class"):
            if isinstance(cls, str) and cls.startswith("language-") and len(cls) > 9:
                return cls
        lang = node.get("data-language")
        if isinstance(lang, str) and lang:
            return f"language-{lang}"
    return None


def _mark(line: Tag) -> str:
    classes = set(line.get_attribute_list("class"))
    if "diff" in classes:
        return next((m for name, m in _DIFF_MARKS.items() if name in classes), " ")
    return " "


def _text(code: Tag) -> str:
    """The lines newline-joined; with any diff line, each line leads with its +/- mark."""
    lines = code.find_all("span", class_="line", recursive=False)
    if not lines:
        return code.get_text()
    texts = [line.get_text() for line in lines]
    marks = [_mark(line) for line in lines]
    if all(m == " " for m in marks):
        return "\n".join(texts)
    return "\n".join(m + t for m, t in zip(marks, texts, strict=True))


def flatten_shiki(root: Tag) -> None:
    """Replace each shiki ``<pre>`` under ``root`` with a plain one, keeping its language."""
    factory = BeautifulSoup("", "html.parser")
    for pre in root.find_all("pre"):
        code = pre.find("code")
        if not isinstance(code, Tag) or code.find(True) is None or not _is_shiki(pre):
            continue
        for popup in code.select(_POPUP_CSS):
            popup.decompose()
        plain = factory.new_tag("code")
        lang = _language(pre, code)
        if lang:
            plain["class"] = lang
        plain.string = _text(code)
        block = factory.new_tag("pre")
        block.append(plain)
        pre.replace_with(block)
