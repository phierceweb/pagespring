"""GitBook acquisition helpers.

GitBook serves a raw-markdown variant of every page (append ``.md``) and an
``llms.txt`` index listing them. That markdown references images as internal
``/files/<id>`` paths that 404 on their own; the real downloadable image lives
behind the rendered page's ``~gitbook/image`` proxy (its ``url=`` param is the
direct, e.g. Firebase-storage, asset URL). So per page we read BOTH the ``.md``
(clean text, ordered image slots) and the rendered HTML (ordered downloadable
image URLs) and resolve each ``/files/<id>`` slot — exactly when the id appears
in a URL, else positionally in document order. Image URLs are left absolute;
remaining root-relative links are absolutized.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Callable

_MD_URL_RE = re.compile(r"https?://[^\s)]+\.md")
_FILES_RE = re.compile(r"/files/[A-Za-z0-9_-]+")
_IMG_PROXY_RE = re.compile(r"""~gitbook/image\?url=([^&"'\s]+)""")
# GitBook's appended footer, in either heading form. Anchored on the whole
# heading line — a doc's own section would carry more words after it.
_FOOTER_RE = re.compile(r"\n#{1,6}\s+Agent Instructions(?::\s+Querying This Documentation)?\s*\n")
# GitBook's llms.txt banner, in either form. The one-line form must match only
# its own line: consuming the blockquote greedily swallows a content blockquote
# that abuts it with no blank line between.
_BANNER_RE = re.compile(
    r"^(?:"
    r"> ## Documentation Index[^\n]*\n(?:>[^\n]*\n?)*"
    r"|> For the complete documentation index[^\n]*\n"
    r")\n*",
    re.MULTILINE,
)
# Other platforms' per-page notes to AI clients: front matter, then leading blocks
# that send the reader to an llms.txt index URL, before the page's first heading.
_SOURCE_COMMENT_RE = re.compile(r"\A<!--[^\n]*-->\n+")
_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n+|\Z)", re.DOTALL)
_LEADING_BLOCK_RE = re.compile(r"\A(.*?)(?:\n[ \t]*\n+|\Z)", re.DOTALL)
_LLMS_URL_RE = re.compile(r"https?://\S+/llms(?:-full)?\.txt", re.IGNORECASE)
_INDEX_POINTER_RE = re.compile(r"\b(?:fetch|index)\b", re.IGNORECASE)
_FENCE_RE = re.compile(r"(?:[ \t>]|[-*+][ \t]|\d{1,9}[.)][ \t])*(`{3,}|~{3,})(.*)")
# A backtick run closed by the next run of the same length, within one paragraph.
_INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)(?:(?!\n[ \t]*\n).)+?(?<!`)\1(?!`)", re.DOTALL)
# Top-level MDX component definitions and imports: JSX source, not prose.
_MDX_IMPORT_RE = re.compile(r"^import\s.+\sfrom\s+['\"][^'\"]+['\"];?\s*$")
_MDX_EXPORT_RE = re.compile(r"^export\s+(?:const|let|function|default)\b")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_MD_TARGET_RE = re.compile(r"\]\(([^)\s]+)")


def discover_pages(llms_txt: str) -> list[str]:
    """Ordered, de-duped per-page .md URLs from the llms.txt index.

    The ``.md`` must be in the path: an index may list in-page anchors whose
    fragment ends in ``.md``, and those are links into a page already listed.
    """
    seen: set[str] = set()
    pages: list[str] = []
    for url in _MD_URL_RE.findall(llms_txt):
        if not urllib.parse.urlparse(url).path.endswith(".md"):
            continue
        if url not in seen:
            seen.add(url)
            pages.append(url)
    return pages


def strip_footer(md: str) -> str:
    """Drop GitBook's appended Agent Instructions footer (either format, see
    _FOOTER_RE) and its preceding `---` rule."""
    md = _FOOTER_RE.split(md, maxsplit=1)[0].rstrip()
    return md[:-3].rstrip() if md.endswith("---") else md


def strip_banner(md: str) -> str:
    """Drop GitBook's per-page llms.txt banner (either format, see _BANNER_RE)."""
    return _BANNER_RE.sub("", md)


def _strip_front_matter(md: str) -> str:
    """``md`` without a leading ``---`` block whose body is a YAML mapping."""
    block = _FRONT_MATTER_RE.match(md)
    if block is None:
        return md
    import yaml  # lazy: only pages opening with a --- block pull this in

    try:
        data = yaml.safe_load(block.group(1))
    except (yaml.YAMLError, ValueError):
        return md
    return md[block.end() :] if isinstance(data, dict) else md


def strip_agent_preamble(md: str) -> str:
    """Drop front matter and the blocks before the first heading that send AI clients
    to an llms.txt index; a leading ``<!-- source -->`` comment is kept."""
    comment = _SOURCE_COMMENT_RE.match(md)
    head = comment.group(0) if comment else ""
    body = _strip_front_matter(md[len(head) :])
    while body:
        block = _LEADING_BLOCK_RE.match(body)
        if block is None or block.group(1).lstrip().startswith("#"):
            break
        text = block.group(1)
        if not (_LLMS_URL_RE.search(text) and _INDEX_POINTER_RE.search(text)):
            break
        body = body[block.end() :]
    return head + body


def _fence_open(line: str) -> str | None:
    """The run of backticks or tildes that opens a code fence on this line, else None."""
    m = _FENCE_RE.fullmatch(line)
    if m is None or (m.group(1)[0] == "`" and "`" in m.group(2)):
        return None
    return m.group(1)


def _fence_closes(line: str, fence: str) -> bool:
    """Whether the line closes ``fence``: the same character, at least as long, nothing after."""
    m = _FENCE_RE.fullmatch(line)
    return (
        m is not None
        and m.group(1)[0] == fence[0]
        and len(m.group(1)) >= len(fence)
        and not m.group(2).strip()
    )


def _outside_code(md: str, rewrite: Callable[[str], str]) -> str:
    """``rewrite`` applied to ``md`` everywhere but fenced code blocks and inline code spans."""
    out: list[str] = []
    prose: list[str] = []

    def flush() -> None:
        text = "".join(prose)
        prose.clear()
        pos = 0
        for span in _INLINE_CODE_RE.finditer(text):
            out.extend((rewrite(text[pos : span.start()]), span.group(0)))
            pos = span.end()
        out.append(rewrite(text[pos:]))

    fence: str | None = None
    for line in re.split(r"(?<=\n)", md):
        bare = line.rstrip("\r\n")
        if fence is not None:
            out.append(line)
            if _fence_closes(bare, fence):
                fence = None
            continue
        fence = _fence_open(bare)
        if fence is None:
            prose.append(line)
        else:
            flush()
            out.append(line)
    flush()
    return "".join(out)


def strip_mdx_definitions(md: str) -> str:
    """Drop top-level MDX ``import`` lines and ``export`` definitions outside code fences.

    An export continues only over code-shaped lines (indented, or opening with a
    closing bracket or tag) up to the one ending in ``;``, so prose is never swallowed."""
    out: list[str] = []
    fence: str | None = None
    skipping = False
    for line in md.split("\n"):
        stripped = line.strip()
        if skipping:
            if stripped and (line[:1].isspace() or stripped.startswith((")", "}", "]", "</"))):
                skipping = not stripped.endswith(";")
                continue
            skipping = False
        if fence is None and _MDX_IMPORT_RE.match(line):
            continue
        if fence is None and _MDX_EXPORT_RE.match(line):
            skipping = not stripped.endswith(";")
            continue
        if fence is None:
            fence = _fence_open(line)
        elif _fence_closes(line, fence):
            fence = None
        out.append(line)
    return "\n".join(out)


def strip_boilerplate(md: str) -> str:
    """Everything a docs platform adds to a page's markdown that isn't the page."""
    return strip_mdx_definitions(strip_agent_preamble(strip_banner(md)))


def page_images(html: str) -> list[str]:
    """Ordered, de-duped content-image download URLs from a rendered page's HTML
    (skips site/space icons and chrome)."""
    out: list[str] = []
    seen: set[str] = set()
    for enc in _IMG_PROXY_RE.findall(html):
        full = urllib.parse.unquote(enc)
        if "%2Ficon%2F" in full or not re.search(r"(?:assets|uploads)%2F", full):
            continue
        key = full.split("?")[0]  # ignore width/dpr/token variants
        if key not in seen:
            seen.add(key)
            out.append(full)
    return out


def resolve_images(md: str, urls: list[str], origin: str) -> str:
    """Rewrite each /files/<id> ref to its real URL: exactly when the id occurs
    in a URL (legacy assets), else positionally from leftover URLs in order."""
    ids = [m.rsplit("/", 1)[1] for m in _FILES_RE.findall(md)]
    url_for: dict[str, str] = {}
    used: set[str] = set()
    for id_ in ids:  # exact pass
        for u in urls:
            if id_ in u and u not in used:
                url_for[id_] = u
                used.add(u)
                break
    leftover = iter([u for u in urls if u not in used])
    for id_ in ids:  # positional pass for the rest
        if id_ not in url_for:
            nxt = next(leftover, None)
            if nxt is not None:
                url_for[id_] = nxt

    def repl(m: re.Match[str]) -> str:
        id_ = m.group(0).rsplit("/", 1)[1]
        return url_for.get(id_, f"{origin}{m.group(0)}")

    return _FILES_RE.sub(repl, md)


def absolutize(md: str, origin: str, page_url: str | None = None) -> str:
    """Root-relative markdown/HTML targets -> absolute on origin; with ``page_url``,
    page-relative markdown targets too. Code blocks and code spans are left as written."""

    def resolve(m: re.Match[str]) -> str:
        target = m.group(1)
        if page_url is None or target.startswith(("/", "#")) or _SCHEME_RE.match(target):
            return m.group(0)
        return f"]({urllib.parse.urljoin(page_url, target)}"

    def rewrite(text: str) -> str:
        text = re.sub(r"\]\((/[^)]*)\)", lambda m: f"]({origin}{m.group(1)})", text)
        text = re.sub(
            r"""((?:src|href)=["'])(/[^"']*)""",
            lambda m: f"{m.group(1)}{origin}{m.group(2)}",
            text,
        )
        return text if page_url is None else _MD_TARGET_RE.sub(resolve, text)

    return _outside_code(md, rewrite)


def process_page(md: str, html: str, origin: str, page_url: str | None = None) -> str:
    """Full per-page transform: strip boilerplate + footer, resolve images, absolutize."""
    md = strip_footer(strip_boilerplate(md.strip()))
    md = resolve_images(md, page_images(html), origin)
    return absolutize(md, origin, page_url)
