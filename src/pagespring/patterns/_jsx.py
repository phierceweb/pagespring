"""JSX syntax inside MDX: where a tag or an expression ends, and what a tag carries.

Every ``skip_*`` returns the index just past the construct, or None when it never
closes — the caller then treats the opening character as text.
"""

from __future__ import annotations

import ast
import html
import re
import textwrap

_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")
_TAG_CLOSE_RE = re.compile(r"\s*/?>")
_TAGGED_TEMPLATE_RE = re.compile(r"\s*([A-Za-z]\w*)`((?:[^`\\$]|\\.|\$(?!\{))*)`\s*", re.DOTALL)
_ASSIGNMENT_RE = re.compile(r"\s*[\w:.-]+=\s*\Z")
_JSX_ATTR_RENAMES = {"className": "class", "htmlFor": "for"}
_JSX_ATTR_RE = re.compile(r"(\s)(className|htmlFor)=")


def _skip_string(src: str, k: int, *, same_line: bool) -> int | None:
    """Past the quote closing the one at ``k``; never across a blank line."""
    line_end = src.find("\n", k) if same_line else -1
    end = src.find(src[k], k + 1, len(src) if line_end < 0 else line_end)
    if end < 0 or (not same_line and _BLANK_LINE_RE.search(src, k, end + 1)):
        return None
    return end + 1


def _skip_template(src: str, k: int, *, ends: dict[int, int | None]) -> int | None:
    """Past the template literal opening at ``k``, ``${}`` holes included."""
    n = len(src)
    k += 1
    while k < n:
        c = src[k]
        if c == "\\":
            k += 2
        elif c == "`":
            return k + 1
        elif src.startswith("${", k):
            end = skip_expression(src, k + 1, ends=ends)
            if end is None:
                return None
            k = end
        else:
            k += 1
    return None


def skip_expression(src: str, i: int, *, ends: dict[int, int | None] | None = None) -> int | None:
    """Past the ``}`` balancing the ``{`` at ``i``.

    JavaScript strings, template literals and comments are skipped whole. A quote
    with no partner on its line is JSX text (an apostrophe), not a string.

    ``ends`` collects the answer for every ``{`` of ``src`` the walk passes; a caller
    trying each ``{`` of one source shares it, or each unclosed one rescans the rest."""
    if ends is None:
        ends = {}
    if i in ends:
        return ends[i]
    n = len(src)
    opened: list[int] = []
    k = i
    while k < n:
        c = src[k]
        end: int | None = k + 1
        if c == "{":
            if k in ends:
                end = ends[k]
            else:
                opened.append(k)
        elif c == "}" and opened:
            ends[opened.pop()] = k + 1
            if not opened:
                return k + 1
        elif c in "\"'":
            end = _skip_string(src, k, same_line=True) or k + 1
        elif c == "`":
            end = _skip_template(src, k, ends=ends)
        elif src.startswith("/*", k):
            close = src.find("*/", k + 2)
            end = None if close < 0 else close + 2
        elif src.startswith("//", k) and src[k - 1 : k] != ":":
            newline = src.find("\n", k)
            end = n if newline < 0 else newline
        if end is None:
            break
        k = end
    for start in opened:
        ends[start] = None
    return None


def skip_tag(src: str, k: int, *, ends: dict[int, int | None] | None = None) -> int | None:
    """Past the ``>`` ending the tag whose attributes start at ``k``.

    A blank line ends the attempt unless the tag closes right after it, so a stray
    ``<Name`` in prose cannot swallow the paragraphs that follow."""
    n = len(src)
    while k < n:
        c = src[k]
        end: int | None = k + 1
        if c == ">":
            return k + 1
        if c == "<" or (
            c == "\n" and _BLANK_LINE_RE.match(src, k) and not _TAG_CLOSE_RE.match(src, k)
        ):
            return None
        if c in "\"'":
            end = _skip_string(src, k, same_line=False)
        elif c == "{":
            end = skip_expression(src, k, ends=ends)
        if end is None:
            return None
        k = end
    return None


def prop_content(tag: str) -> str:
    """What a component tag's props carry as content: a tagged-template prop as a fenced
    block in the tag's language, a literal array of string rows as a table."""
    blocks: list[str] = []
    k = tag.find("={")
    while k >= 0:
        end = skip_expression(tag, k + 1)
        if end is None:
            break
        sample = _TAGGED_TEMPLATE_RE.fullmatch(tag, k + 2, end - 1)
        if sample is not None:
            code = textwrap.dedent(sample.group(2)).strip("\n")
            blocks.append(f"\n```{sample.group(1)}\n{code}\n```\n")
        else:
            blocks.append(_table(tag[k + 2 : end - 1]))
        k = tag.find("={", end)
    return "".join(blocks)


def _table(value: str) -> str:
    """``value`` as an HTML table when it is a literal array of string rows, else ""."""
    try:
        rows = ast.literal_eval(value.strip())
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return ""
    if not (
        isinstance(rows, list)
        and rows
        and all(isinstance(row, list) and all(isinstance(c, str) for c in row) for row in rows)
    ):
        return ""
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(c, quote=False)}</td>" for c in row) + "</tr>\n"
        for row in rows
    )
    return f"\n<table>\n{body}</table>\n"


def clean_html_tag(tag: str) -> str:
    """An HTML tag with expression-valued and spread attributes dropped, JSX names renamed."""
    parts: list[str] = []
    k = 0
    while k < len(tag):
        c = tag[k]
        if c in "\"'":
            close = tag.find(c, k + 1)
            end = len(tag) if close < 0 else close + 1
            parts.append(tag[k:end])
            k = end
        elif c == "{":
            before = "".join(parts)
            dropped = _ASSIGNMENT_RE.sub("", before)
            parts = [dropped if dropped != before else before.rstrip()]
            k = skip_expression(tag, k) or len(tag)
        else:
            parts.append(c)
            k += 1
    return _JSX_ATTR_RE.sub(
        lambda m: f"{m.group(1)}{_JSX_ATTR_RENAMES[m.group(2)]}=", "".join(parts)
    )
