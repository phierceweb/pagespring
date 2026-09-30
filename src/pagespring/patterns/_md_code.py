"""Where code sits in markdown: fenced and indented blocks, and inline spans, so a
rewrite of the prose around them can leave them as written."""

from __future__ import annotations

import re
from collections.abc import Callable

_FENCE_RE = re.compile(r"(?:[ \t>]|[-*+][ \t]|\d{1,9}[.)][ \t])*(`{3,}|~{3,})(.*)")
_LIST_ITEM_RE = re.compile(r"( *)([-*+]|\d{1,9}[.)])( +|$)")
# A line opening or closing an HTML or JSX element. MDX has no indented code blocks,
# and indentation under markup is nesting.
_MARKUP_LINE_RE = re.compile(r"^[ \t]*</?[A-Za-z][\w.-]*(?:[\s/>]|$)", re.MULTILINE)
# A backtick run closed by the next run of the same length, within one paragraph.
_INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)(?:(?!\n[ \t]*\n).)+?(?<!`)\1(?!`)", re.DOTALL)
_PLACEHOLDER_RE = re.compile(r"\0(\d+)\0")


def fence_open(line: str) -> str | None:
    """The run of backticks or tildes that opens a code fence on this line, else None."""
    m = _FENCE_RE.fullmatch(line)
    if m is None or (m.group(1)[0] == "`" and "`" in m.group(2)):
        return None
    return m.group(1)


def fence_closes(line: str, fence: str) -> bool:
    """Whether the line closes ``fence``: the same character, at least as long, nothing after."""
    m = _FENCE_RE.fullmatch(line)
    return (
        m is not None
        and m.group(1)[0] == fence[0]
        and len(m.group(1)) >= len(fence)
        and not m.group(2).strip()
    )


class _IndentedCode:
    """Tracks indented code blocks line by line: after a blank line, indented four columns
    past the content of the enclosing list item. Off on a page carrying markup."""

    def __init__(self, md: str) -> None:
        self.enabled = _MARKUP_LINE_RE.search(md) is None
        self.items: list[int] = []  # content column of each open list item
        self.code_at: int | None = None
        self.after_blank = True

    def claims(self, line: str) -> bool:
        """Whether ``line`` is indented code; fed every line outside fences, in order."""
        if not self.enabled:
            return False
        line = line.expandtabs(4)
        if not line.strip():
            self.after_blank = True
            return self.code_at is not None
        col = len(line) - len(line.lstrip(" "))
        if self.code_at is not None and col >= self.code_at:
            return True
        self.code_at = None
        after_blank, self.after_blank = self.after_blank, False
        item = _LIST_ITEM_RE.match(line)
        if after_blank or item is not None:
            while self.items and self.items[-1] > col:
                self.items.pop()
        base = self.items[-1] if self.items else 0
        if after_blank and col >= base + 4 and fence_open(line) is None:
            self.code_at = base + 4
            return True
        if item is not None:
            gap = len(item.group(3))
            self.items.append(item.end(2) + (gap if 1 <= gap <= 4 else 1))
        return False


def outside_code(md: str, rewrite: Callable[[str], str]) -> str:
    """``rewrite`` applied to ``md`` everywhere but code blocks and inline code spans.

    ``rewrite`` sees each code span as a placeholder, so a link labelled with code
    reaches it whole."""
    out: list[str] = []
    prose: list[str] = []

    def flush() -> None:
        spans: list[str] = []

        def hide(m: re.Match[str]) -> str:
            spans.append(m.group(0))
            return f"\0{len(spans) - 1}\0"

        def show(m: re.Match[str]) -> str:
            i = int(m.group(1))
            return spans[i] if i < len(spans) else m.group(0)

        text = rewrite(_INLINE_CODE_RE.sub(hide, "".join(prose)))
        prose.clear()
        out.append(_PLACEHOLDER_RE.sub(show, text))

    fence: str | None = None
    indented = _IndentedCode(md)
    for line in re.split(r"(?<=\n)", md):
        bare = line.rstrip("\r\n")
        if fence is not None:
            out.append(line)
            if fence_closes(bare, fence):
                fence = None
            continue
        if indented.claims(bare):
            flush()
            out.append(line)
            continue
        fence = fence_open(bare)
        if fence is None:
            prose.append(line)
        else:
            flush()
            out.append(line)
    flush()
    return "".join(out)
