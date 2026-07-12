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

_MD_URL_RE = re.compile(r"https?://[^\s)]+\.md")
_FILES_RE = re.compile(r"/files/[A-Za-z0-9_-]+")
_IMG_PROXY_RE = re.compile(r"""~gitbook/image\?url=([^&"'\s]+)""")
# GitBook's appended footer, both shapes seen in the wild: the pre-2026 single
# combined heading, and the current standalone "# Agent Instructions" heading
# (exact heading line — a doc's own section would have more words after it).
_FOOTER_RE = re.compile(r"\n#{1,6}\s+Agent Instructions(?::\s+Querying This Documentation)?\s*\n")
# GitBook's per-page leading banner pointing agents at llms.txt, both shapes
# seen in the wild: the "> ## Documentation Index" block (multi-line), and the
# 2026 single-paragraph "> For the complete documentation index, …" form. The
# 2026 form is exactly one blockquote line — matching only that line keeps a
# legit content blockquote that directly abuts it (no blank line) from being
# swallowed. The legacy form is an intrinsically multi-line block.
_BANNER_RE = re.compile(
    r"^(?:"
    r"> ## Documentation Index[^\n]*\n(?:>[^\n]*\n?)*"
    r"|> For the complete documentation index[^\n]*\n"
    r")\n*",
    re.MULTILINE,
)


def discover_pages(llms_txt: str) -> list[str]:
    """Ordered, de-duped per-page .md URLs from the llms.txt index."""
    seen: set[str] = set()
    pages: list[str] = []
    for url in _MD_URL_RE.findall(llms_txt):
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


def absolutize(md: str, origin: str) -> str:
    """Any remaining root-relative markdown/HTML targets -> absolute on origin."""
    md = re.sub(r"\]\((/[^)]*)\)", lambda m: f"]({origin}{m.group(1)})", md)
    md = re.sub(
        r"""((?:src|href)=["'])(/[^"']*)""",
        lambda m: f"{m.group(1)}{origin}{m.group(2)}",
        md,
    )
    return md


def process_page(md: str, html: str, origin: str) -> str:
    """Full per-page transform: strip banner + footer, resolve images, absolutize."""
    md = strip_footer(strip_banner(md.strip()))
    md = resolve_images(md, page_images(html), origin)
    return absolutize(md, origin)
