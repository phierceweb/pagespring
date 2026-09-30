"""Docsify ``:include`` links, replaced by the files they embed.

Markdown is inlined, ``.mmd`` becomes a mermaid fence and any other text file a
fence in its extension's language. Media and HTML embeds, and files that fail to
load, stay links.
"""

from __future__ import annotations

import re
import textwrap
from urllib.parse import urlparse

from pf_core.log import get_logger

from pagespring import http
from pagespring.patterns._docsify_pages import is_app_shell
from pagespring.patterns._docsify_routes import Site, asset_url, link_options
from pagespring.patterns._md_code import outside_code

log = get_logger(__name__)

_INCLUDE_RE = re.compile(
    r"(?<!!)\[(?:[^\[\]\n]|\[[^\]\n]*\])*\]\(\s*<?(?P<target>[^\s)>]+)>?\s+"
    r"(?P<q>['\"])(?P<title>[^'\"\n]*)(?P=q)\s*\)"
)
_EMBED_BY_EXTENSION = (
    (re.compile(r"\.(?:md|markdown)$"), "markdown"),
    (re.compile(r"\.mmd$"), "mermaid"),
    (re.compile(r"\.html?$"), "iframe"),
    (re.compile(r"\.(?:mp4|ogg)$"), "video"),
    (re.compile(r"\.mp3$"), "audio"),
)
_EMBED_TYPES = {kind for _ext, kind in _EMBED_BY_EXTENSION} | {"code"}
_INLINED = {"markdown", "mermaid", "code"}


def _embed_type(url: str, declared: str | bool | None) -> str:
    if isinstance(declared, str) and declared in _EMBED_TYPES:
        return declared
    path = urlparse(url).path.lower()
    return next((kind for ext, kind in _EMBED_BY_EXTENSION if ext.search(path)), "code")


def _fragment(text: str, name: str, *, omit_lines: bool) -> str | None:
    """The text between two ``### [name]`` or ``/// [name]`` markers; with
    ``omit_lines``, between the lines that hold them."""
    marker = rf"(?:###|///)\s*\[{re.escape(name)}\]"
    # :omitFragmentLine is Docsify 5's, which takes the first pair; 4 takes the outermost.
    if omit_lines:
        # Line by line: a pattern matching up to the marker line backtracks through long lines.
        lines = text.split("\n")
        held = [i for i, line in enumerate(lines) if re.search(marker, line)][:2]
        body = "\n".join(lines[held[0] + 1 : held[1]]) if len(held) == 2 else None
    else:
        m = re.search(rf"{marker}(.*){marker}", text, re.S)
        body = m.group(1) if m else None
    return textwrap.dedent(body).strip() if body is not None else None


def _fenced(text: str, lang: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    body = text.strip("\n")
    return f"{fence}{lang}\n{body}\n{fence}"


def _load(url: str, loaded: dict[str, str | None]) -> str | None:
    """An embedded file's text, fetched once per acquire; None when it fails to load."""
    if url not in loaded:
        http.polite_sleep()
        text: str | None
        try:
            _final, text = http.fetch_text(url)
        except Exception as exc:
            log.warning("docsify.include_error", url=url, error=str(exc))
            text = None
        if text is not None and is_app_shell(text) and not url.endswith((".html", ".htm")):
            log.warning("docsify.include_missing", url=url)
            text = None
        loaded[url] = text
    return loaded[url]


def _block(text: str, m: re.Match[str]) -> str:
    """``text`` set off by blank lines from what surrounds the link it replaces."""
    head, tail = m.string[: m.start()], m.string[m.end() :]
    before = 2 - (len(head) - len(head.rstrip("\n"))) if head else 0
    after = 2 - (len(tail) - len(tail.lstrip("\n"))) if tail else 0
    return "\n" * max(0, before) + text.strip() + "\n" * max(0, after)


def embed(md: str, route: str, site: Site, loaded: dict[str, str | None]) -> str:
    """``md`` with each ``:include`` link outside code replaced by the file it embeds."""

    def include(m: re.Match[str]) -> str:
        options = link_options(m.group("title"))
        if "include" not in options:
            return m.group(0)
        url = asset_url(m.group("target"), route, site)
        kind = _embed_type(url, options.get("type"))
        text = _load(url, loaded) if kind in _INLINED else None
        fragment = options.get("fragment")
        if text is not None and isinstance(fragment, str):
            text = _fragment(text, fragment, omit_lines="omitFragmentLine" in options)
        if text is None:
            start, end = m.start("target") - m.start(), m.end("target") - m.start()
            return m.group(0)[:start] + url + m.group(0)[end:]
        if kind == "code":
            ext = re.search(r"\.(\w+)$", urlparse(url).path)
            text = _fenced(text, ext.group(1) if ext else "")
        elif kind == "mermaid":
            text = _fenced(text, "mermaid")
        return _block(text, m)

    return outside_code(md, lambda text: _INCLUDE_RE.sub(include, text))
