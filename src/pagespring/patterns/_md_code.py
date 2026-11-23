"""Where code sits in markdown: fenced and indented blocks, and inline spans, so a
rewrite of the prose around them can leave them as written."""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Callable

_FENCE_RE = re.compile(r"(?:[ \t>]|[-*+][ \t]|\d{1,9}[.)][ \t])*(`{3,}|~{3,})(.*)")
_LIST_ITEM_RE = re.compile(r"( *)([-*+]|\d{1,9}[.)])( +|$)")
# A line opening or closing an HTML or JSX element. MDX has no indented code blocks,
# and indentation under markup is nesting.
_MARKUP_LINE_RE = re.compile(r"^[ \t]*</?[A-Za-z][\w.-]*(?:[\s/>]|$)", re.MULTILINE)
_RUN_RE = re.compile(r"`+")
_ESCAPE_OR_RUN_RE = re.compile(r"\\[\s\S]|`+")
_PARAGRAPH_END_RE = re.compile(r"(?=\n[ \t]*\n)")
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


def _code_spans(text: str) -> list[tuple[int, int]]:
    """Inline code spans, read left to right: a backtick run, less a first backtick a backslash
    escapes, opens one when the next run of that length comes before a blank line; else it is text."""
    starts: dict[int, list[int]] = {}
    for run in _RUN_RE.finditer(text):
        starts.setdefault(len(run[0]), []).append(run.start())
    ends = [m.start() for m in _PARAGRAPH_END_RE.finditer(text)]
    spans: list[tuple[int, int]] = []
    pos = 0
    while (m := _ESCAPE_OR_RUN_RE.search(text, pos)) is not None:
        pos = m.end()
        if m[0][0] == "\\":
            continue
        closers = starts.get(len(m[0]), [])
        nearest = bisect_left(closers, pos)
        end = bisect_left(ends, pos)
        if nearest < len(closers) and (end == len(ends) or ends[end] >= closers[nearest]):
            pos = closers[nearest] + len(m[0])
            spans.append((m.start(), pos))
    return spans


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
    """``rewrite`` applied to ``md`` outside code blocks and spans; each code span reaches it as a
    placeholder, so a link labelled with code arrives whole."""
    out: list[str] = []
    prose: list[str] = []

    def flush() -> None:
        text = "".join(prose)
        prose.clear()
        spans: list[str] = []
        hidden: list[str] = []
        pos = 0
        for start, end in _code_spans(text):
            hidden.append(f"{text[pos:start]}\0{len(spans)}\0")
            spans.append(text[start:end])
            pos = end
        hidden.append(text[pos:])

        def show(m: re.Match[str]) -> str:
            i = int(m.group(1))
            return spans[i] if i < len(spans) else m.group(0)

        out.append(_PLACEHOLDER_RE.sub(show, rewrite("".join(hidden))))

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


def prose_outside_code(md: str) -> str:
    """``md``'s text outside code blocks and spans, each span left as a space."""
    chunks: list[str] = []

    def keep(text: str) -> str:
        chunks.append(_PLACEHOLDER_RE.sub(" ", text))
        return text

    outside_code(md, keep)
    return "".join(chunks)
