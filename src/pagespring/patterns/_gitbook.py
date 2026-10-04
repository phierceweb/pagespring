"""GitBook helpers: a page's ``.md`` points images at ``/files/<id>`` paths that 404, so the
rendered page's ``~gitbook/image`` URLs fill those slots, by id, else by position."""

from __future__ import annotations

import re
import urllib.parse

from pagespring.patterns._md_code import fence_closes, fence_open, outside_code
from pagespring.patterns._site import under_section

_MD_URL_RE = re.compile(r"https?://[^\s)]+\.md")
_FILES_RE = re.compile(r"/files/[A-Za-z0-9_-]+")
_IMG_PROXY_RE = re.compile(r"""~gitbook/image\?url=([^&"'\s]+)""")
# GitBook's appended footer, in either heading form. Anchored on the whole
# heading line — a doc's own section would carry more words after it.
_FOOTER_RE = re.compile(r"\n#{1,6}\s+Agent Instructions(?::\s+Querying This Documentation)?\s*\n")
# GitBook's llms.txt banner in either form. The one-line form matches only its own line, so a
# content blockquote abutting it survives.
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
# Front-matter keys are lowercase, quoted, or a common key in any case (Title); a
# callout framed by rules ("Note: ...") or a prose line is not one.
_FRONT_MATTER_KEY_RE = re.compile(
    r"""(?:[a-z_][\w.-]*(?::[\w.-]+)*|"[^"\n]+"|'[^'\n]+'"""
    r"|(?i:title|description|date|author|authors|tags|categories|keywords|slug|weight|draft))"
    r"[ \t]*:(?:[ \t]|$)"
)
_LEADING_BLOCK_RE = re.compile(r"\A(.*?)(?:\n[ \t]*\n+|\Z)", re.DOTALL)
_LLMS_URL_RE = re.compile(r"https?://\S+/llms(?:-full)?\.txt", re.IGNORECASE)
_INDEX_POINTER_RE = re.compile(r"\b(?:fetch|index)\b", re.IGNORECASE)
# Docsy's pointer under the title and description, closed by a rule.
_TITLED_POINTER_RE = re.compile(
    r"\A(#[^\n]*\n+(?:>[^\n]*\n+)*(?:---[ \t]*\n+)?)"
    r"LLMS index: \[llms(?:-full)?\.txt\]\([^)\s]+\)[ \t]*\n+---[ \t]*(?:\n+|\Z)"
)
# Top-level MDX component definitions and imports: JSX source, not prose.
_MDX_IMPORT_RE = re.compile(r"^import\s.+\sfrom\s+['\"][^'\"]+['\"];?\s*$")
_MDX_EXPORT_RE = re.compile(r"^export\s+(?:const|let|function|default)\b")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_MD_TARGET_RE = re.compile(r"\]\(([^)\s]+)")


def discover_pages(llms_txt: str, section: str | None = None) -> list[str]:
    """Ordered, de-duped page ``.md`` URLs from the llms.txt index (``.md`` in the path, not a
    fragment's anchor); with ``section``, only that page and those beneath it."""
    seen: set[str] = set()
    pages: list[str] = []
    for url in _MD_URL_RE.findall(llms_txt):
        if not urllib.parse.urlparse(url).path.endswith(".md"):
            continue
        if section is not None and not under_section(url.removesuffix(".md"), section):
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


def _yaml_shaped(body: str) -> bool:
    """Opens on a key line, and every other line is a key, a list item, indented or blank; a ``#``
    line is a YAML comment only in a run holding a key, since a heading stands apart."""
    lines = body.split("\n")
    comments: set[int] = set()
    run: list[int] = []
    for i, line in enumerate([*lines, ""]):
        if line.strip():
            run.append(i)
            continue
        if any(_FRONT_MATTER_KEY_RE.match(lines[j]) for j in run):
            comments.update(j for j in run if lines[j].startswith("#"))
        run = []
    first, *rest = [line for i, line in enumerate(lines) if i not in comments]
    return bool(_FRONT_MATTER_KEY_RE.match(first)) and all(
        not line.strip()
        or line[0] in " \t"
        or line.startswith("- ")
        or _FRONT_MATTER_KEY_RE.match(line)
        for line in rest
    )


def split_front_matter(md: str) -> tuple[dict[str, object] | None, str]:
    """A leading ``---`` block of key lines that parses as a YAML mapping, and the rest
    of ``md``; ``(None, md)`` when there is none."""
    block = _FRONT_MATTER_RE.match(md)
    if block is None or not _yaml_shaped(block.group(1)):
        return None, md
    import yaml  # lazy: only pages opening with a --- block pull this in

    try:
        data = yaml.safe_load(block.group(1))
    except (yaml.YAMLError, ValueError):
        return None, md
    return (data, md[block.end() :]) if isinstance(data, dict) else (None, md)


def _strip_front_matter(md: str) -> str:
    return split_front_matter(md)[1]


def lead_with_front_matter_title(md: str) -> str:
    """``md`` without its front matter, opening on the block's ``title`` as an H1 unless it
    already opens on one; a leading ``<!-- source -->`` comment stays first."""
    comment = _SOURCE_COMMENT_RE.match(md)
    head = comment.group(0) if comment else ""
    data, body = split_front_matter(md[len(head) :])
    if data is None:
        return md
    title = data.get("title")
    if isinstance(title, str) and title.strip() and not body.lstrip().startswith("# "):
        body = f"# {title.strip()}\n\n{body}"
    return head + body


def strip_agent_preamble(md: str) -> str:
    """Drop front matter and the leading blocks, or the pointer under the first heading, that send
    AI clients to an llms.txt index; a leading ``<!-- source -->`` comment stays."""
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
    return head + _TITLED_POINTER_RE.sub(r"\1", body, count=1)


def strip_mdx_definitions(md: str) -> str:
    """Drop top-level MDX imports and exports outside fences; an export runs only over code-shaped
    lines to the one ending in ``;``, so prose is never swallowed."""
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
            fence = fence_open(line)
        elif fence_closes(line, fence):
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

    return outside_code(md, rewrite)


def process_page(md: str, html: str, origin: str, page_url: str | None = None) -> str:
    """Full per-page transform: strip boilerplate + footer, resolve images, absolutize."""
    md = strip_footer(strip_boilerplate(md.strip()))
    md = resolve_images(md, page_images(html), origin)
    return absolutize(md, origin, page_url)
