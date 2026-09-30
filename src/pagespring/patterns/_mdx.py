"""MDX source to plain markdown: the JSX goes, the prose stays.

Capitalized components and fragments lose their tags and keep their children; a
line-alone component's ``title`` prop becomes a bold line (a link when it has an
``href``), a code sample passed as a tagged template prop (``example={css`...`}``) a
fenced block, and a literal array of string rows (``rows={[["a", "b"]]}``) a table.
Expressions are dropped unless they are a string literal, a fragment of text, or markup
outside any component (a component's markup child is a rendered demo). Lowercase tags
are HTML and stay, minus JSX-only attribute syntax. MDX has no indented code blocks, so
the indent that only nested children inside a dropped block tag is removed. Front
matter, fenced code and code spans are left as written; markup that never closes is left
as text.
"""

from __future__ import annotations

import functools
import html
import re

from pagespring.patterns._gitbook import strip_mdx_definitions
from pagespring.patterns._jsx import clean_html_tag, prop_content, skip_expression, skip_tag
from pagespring.patterns._md_code import fence_closes, fence_open

_HEAD_RE = re.compile(
    r"\A(?:<!--[^\n]*-->\n+)?(?:---[ \t]*\n[\w-]+:.*?\n---[ \t]*(?:\n|\Z))?", re.DOTALL
)
_EXPORT_RE = re.compile(r"""export\s+const\s+(title|description)\s*=\s*(["'])(.*?)\2\s*;?\s*""")
_H1_RE = re.compile(r" {0,3}#(?:[ \t]|$)")
_IMPORT_OPEN_RE = re.compile(r"import\s+(?:type\s+)?(?:[\w$]+\s*,\s*)?\{\s*")
_IMPORT_NAME_RE = re.compile(r"\s*(?:type\s+)?[\w$]+(?:\s+as\s+[\w$]+)?\s*,?\s*")
_IMPORT_CLOSE_RE = re.compile(r"""\s*\}\s*from\s+(["'])[^"']+\1\s*;?\s*""")
_SPECIAL_RE = re.compile(r"[\n\\`{<]")
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")
_FRAGMENT_RE = re.compile(r"</?>")
_COMPONENT_RE = re.compile(r"<(/?)([A-Z][\w.]*|[a-z]\w*(?:\.\w+)+)(?=[\s/>])")
_HTML_TAG_RE = re.compile(r"</?[a-z][a-z0-9-]*(?=[\s/>])")
_TITLE_RE = re.compile(r"""\stitle=(?:"([^"]*)"|'([^']*)')""")
_HREF_RE = re.compile(r"""\shref=(?:"([^"]+)"|'([^']+)')""")
_STRING_RE = re.compile(r"""(["'])((?:(?!\1)[^\\\n]|\\.)*)\1|`((?:[^`\\$]|\\.|\$(?!\{))*)`""")
_BACKTICKS_RE = re.compile(r"`+")
_INDENTED_LINE_RE = re.compile(r"^([ \t]*)\S", re.MULTILINE)


def mdx_to_markdown(md: str) -> str:
    """The page as plain markdown: MDX definitions and JSX removed, prose kept."""
    md = md.replace("\r\n", "\n")
    head_match = _HEAD_RE.match(md)
    head = head_match.group(0) if head_match else ""
    body = strip_mdx_definitions(_definitions_as_prose(md[len(head) :]))
    return _tidy(head + strip_jsx(body))


def strip_jsx(md: str) -> str:
    """``md`` with JSX components, fragments and expressions removed (see module doc)."""
    return _Scanner(md).run()


def _definitions_as_prose(md: str) -> str:
    """Exported string ``title``/``description`` as a heading and a lead paragraph —
    the title only when the page has no H1 of its own — and multi-line imports dropped."""
    lines = md.split("\n")
    prose = _prose_flags(lines)
    has_h1 = any(flag and _H1_RE.match(line) for line, flag in zip(lines, prose, strict=True))
    out: list[str] = []
    skip_to = 0
    for n, (line, flag) in enumerate(zip(lines, prose, strict=True)):
        if n < skip_to:
            continue
        if flag and _IMPORT_OPEN_RE.fullmatch(line):
            skip_to = _import_end(lines, n + 1)
            if skip_to > n:
                continue
        m = _EXPORT_RE.fullmatch(line) if flag else None
        if m is None:
            out.append(line)
        elif m.group(1) == "description":
            out.extend((_unescape(m.group(3)), ""))
        elif not has_h1:
            out.extend((f"# {_unescape(m.group(3))}", ""))
    return "\n".join(out)


def _import_end(lines: list[str], start: int) -> int:
    """Index past the ``} from "…"`` line closing an import opened just before ``start``,
    or 0 when a line between is not an imported name."""
    for n in range(start, len(lines)):
        if _IMPORT_CLOSE_RE.fullmatch(lines[n]):
            return n + 1
        if not _IMPORT_NAME_RE.fullmatch(lines[n]):
            return 0
    return 0


def _prose_flags(lines: list[str]) -> list[bool]:
    """Per line, whether it lies outside fenced code."""
    flags: list[bool] = []
    fence: str | None = None
    for line in lines:
        if fence is not None:
            flags.append(False)
            if fence_closes(line, fence):
                fence = None
            continue
        fence = fence_open(line)
        flags.append(fence is None)
    return flags


def _tidy(md: str) -> str:
    """Blank lines outside code collapsed to one, none leading, one newline at the end."""
    out: list[str] = []
    fence: str | None = None
    for line in md.split("\n"):
        if fence is not None:
            out.append(line)
            if fence_closes(line, fence):
                fence = None
            continue
        if not line.strip():
            if out and out[-1]:
                out.append("")
            continue
        out.append(line)
        fence = fence_open(line)
    return "\n".join(out).rstrip("\n") + "\n"


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def _expression_text(inner: str, *, markup: bool) -> str:
    """What an expression contributes to the prose: a string's text, a fragment's, or
    when ``markup`` is allowed, the markup it holds."""
    s = inner.strip()
    string = _STRING_RE.fullmatch(s)
    if string is not None:
        text = string.group(2) if string.group(2) is not None else string.group(3)
        return html.escape(_unescape(text), quote=False)
    if s.startswith("<") and s.endswith(">") and (markup or s.startswith("<>")):
        return strip_jsx(s).strip()
    return ""


def _line_bounds(src: str, i: int, j: int) -> tuple[bool, int]:
    """(whether ``src[i:j]`` is alone on its line, index of that line's end)."""
    start = src.rfind("\n", 0, i) + 1
    end = src.find("\n", j)
    end = len(src) if end < 0 else end
    return not src[start:i].strip() and not src[j:end].strip(), end


def _next_indent(src: str, pos: int) -> int:
    """Leading-whitespace width of the first non-blank line from ``pos``, a line start."""
    line = _INDENTED_LINE_RE.search(src, pos)
    return len(line.group(1)) if line else 0


@functools.cache
def _closing_backticks(run: int) -> re.Pattern[str]:
    return re.compile(rf"(?<!`)`{{{run}}}(?!`)")


class _Scanner:
    """One pass over MDX source, line by line, emitting markdown."""

    def __init__(self, src: str) -> None:
        self.src = src
        self.out: list[str] = []
        # Open block components: (name, indent of their children).
        self.blocks: list[tuple[str, int]] = []
        self.ends: dict[int, int | None] = {}  # skip_expression's answers over src

    def run(self) -> str:
        i = 0
        while i < len(self.src):
            i = self._line(i)
        return "".join(self.out)

    def _dedent(self, i: int) -> int:
        width = self.blocks[-1][1] if self.blocks else 0
        stop = i
        while stop - i < width and stop < len(self.src) and self.src[stop] in " \t":
            stop += 1
        return stop

    def _line(self, i: int) -> int:
        i = self._dedent(i)
        end = self.src.find("\n", i)
        end = len(self.src) if end < 0 else end
        fence = fence_open(self.src[i:end])
        if fence is None:
            return self._prose(i)
        self.out.append(self.src[i : end + 1])
        k = end + 1
        while k < len(self.src):
            k = self._dedent(k)
            end = self.src.find("\n", k)
            end = len(self.src) if end < 0 else end
            self.out.append(self.src[k : end + 1])
            closed = fence_closes(self.src[k:end], fence)
            k = end + 1
            if closed:
                break
        return k

    def _prose(self, i: int) -> int:
        src = self.src
        while i < len(src):
            m = _SPECIAL_RE.search(src, i)
            stop = m.start() if m else len(src)
            self.out.append(src[i:stop])
            i = stop
            if i >= len(src):
                break
            c = src[i]
            if c == "\n":
                self.out.append(c)
                return i + 1
            if c == "\\":
                width = 1 if src[i + 1 : i + 2] in ("", "\n") else 2
                self.out.append(src[i : i + width])
                i += width
            elif c == "`":
                i = self._code_span(i)
            else:
                end = self._expression(i) if c == "{" else self._tag(i)
                if end is None:
                    self.out.append(c)
                    i += 1
                else:
                    i = end
        return i

    def _code_span(self, i: int) -> int:
        run = _BACKTICKS_RE.match(self.src, i)
        assert run is not None
        para = _BLANK_LINE_RE.search(self.src, i)
        close = _closing_backticks(len(run.group(0))).search(
            self.src, run.end(), para.start() if para else len(self.src)
        )
        end = close.end() if close else run.end()
        self.out.append(self.src[i:end])
        return end

    def _expression(self, i: int) -> int | None:
        end = skip_expression(self.src, i, ends=self.ends)
        if end is not None:
            self.out.append(_expression_text(self.src[i + 1 : end - 1], markup=not self.blocks))
        return end

    def _tag(self, i: int) -> int | None:
        src = self.src
        if src.startswith("<!--", i):
            close = src.find("-->", i + 4)
            if close < 0:
                return None
            self.out.append(src[i : close + 3])
            return close + 3
        fragment = _FRAGMENT_RE.match(src, i)
        if fragment is not None:
            return fragment.end()
        component = _COMPONENT_RE.match(src, i)
        if component is not None:
            end = skip_tag(src, component.end(), ends=self.ends)
            if end is not None:
                self._component(i, end, component.group(2), closing=bool(component.group(1)))
            return end
        html_tag = _HTML_TAG_RE.match(src, i)
        if html_tag is None:
            return None
        end = skip_tag(src, html_tag.end(), ends=self.ends)
        if end is not None:
            self.out.append(clean_html_tag(src[i:end]))
        return end

    def _component(self, i: int, j: int, name: str, *, closing: bool) -> None:
        src = self.src
        alone, line_end = _line_bounds(src, i, j)
        if closing:
            for depth in range(len(self.blocks) - 1, -1, -1):
                if self.blocks[depth][0] == name:
                    del self.blocks[depth:]
                    break
            following = _COMPONENT_RE.match(src, j)
            if following is not None and not following.group(1):
                self.out.append(" ")
            return
        if alone:
            title = _TITLE_RE.search(src, i, j)
            text = title.group(1) or title.group(2) if title else None
            href = _HREF_RE.search(src, i, j)
            if text and href:
                text = f"[{text}]({href.group(1) or href.group(2)})"
            if text:
                self.out.append(f"\n**{text}**\n")
        self.out.append(prop_content(src[i:j]))
        if alone and src[j - 2] != "/":
            self.blocks.append((name, _next_indent(src, line_end + 1)))
