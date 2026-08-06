"""Paligo acquisition for docs_probe — search-index driven, no crawl.

Paligo HTML5 output has two faces. A **topic** page announces itself with
``<meta name="generator" content="Paligo">``; the **portal** shell a reader
actually lands on carries no generator meta, no ``<main>``, and none of the
content — only links into ``<locale>/``. Probing the portal alone therefore
identifies nothing, which is why a Paligo manual reads as an unrecognized site.

The page index is the search corpus, ``<base>/js/fuzzydata.js``. Its entries are
per *anchor*, not per page, so they dedupe down to the real page set — reading
it replaces a crawl.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns._site import absolutize_refs, flatten_responsive_images, strip_scripts

log = get_logger(__name__)

_MAX_PAGES = 5000
# An empty search shell sits beside the content article under the same tag;
# picking the wrong one yields a silently empty corpus.
_CONTENT_CSS = "article.topic.content-container"
_PORTAL_TELLS = ("portal-single-publication", "html5.fuse.search.js", "fuzzydata")
_LOCALE_LINK_RE = re.compile(r'href="([a-z]{2}(?:-[A-Za-z]{2})?)/[^"/]+\.html"')
_URL_RE = re.compile(r'"url"\s*:\s*"([^"]+)"')


def is_paligo(html: str) -> bool:
    """True for a Paligo topic (generator meta) or its portal shell (tells)."""
    if re.search(r'name="generator"\s+content="paligo"', html, re.I):
        return True
    return sum(tell in html for tell in _PORTAL_TELLS) >= 2


def publication_base(url: str, html: str) -> str:
    """The directory holding the topics and ``js/fuzzydata.js``.

    A topic URL already sits in it. A portal URL does not — the locale dir is
    whatever the portal links into, which is not always ``en``.
    """
    here = url.rsplit("/", 1)[0]
    if re.search(r'name="generator"\s+content="paligo"', html, re.I):
        return here
    locale = _LOCALE_LINK_RE.search(html)
    return f"{here}/{locale.group(1)}" if locale else here


def _pages(fuzzy_js: str) -> list[str]:
    """Page filenames in first-seen order; anchors collapse onto their page."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in _URL_RE.findall(fuzzy_js):
        page = raw.split("#", 1)[0]
        if page and page not in seen:
            seen.add(page)
            out.append(page)
    return out


def _extract(page_html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(page_html, "html.parser")
    node = soup.select_one(_CONTENT_CSS)
    if not isinstance(node, Tag):
        return None
    strip_scripts(node)
    flatten_responsive_images(node)
    absolutize_refs(node, page_url)
    return str(node)


def acquire(url: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    _f, entry_html = http.fetch_text(url)
    base = publication_base(url, entry_html)
    index_url = f"{base}/js/fuzzydata.js"
    try:
        _f2, fuzzy = http.fetch_text(index_url)
    except Exception as exc:
        raise InvalidInputError(
            f"{index_url} is not fetchable — a Paligo publication keeps its whole "
            "page list in fuzzydata.js; without it the topics cannot be enumerated."
        ) from exc

    pages = _pages(fuzzy)
    if not pages:
        raise InvalidInputError(f"{index_url} lists no pages — not a Paligo search index?")
    truncated = len(pages) > _MAX_PAGES
    if truncated:
        log.warning("paligo.capped", found=len(pages), cap=_MAX_PAGES)
        pages = pages[:_MAX_PAGES]

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    lost = 0
    for i, name in enumerate(pages):
        page_url = f"{base}/{name}"
        try:
            final, body = http.fetch_text(page_url)
        except Exception as exc:
            lost += 1
            log.warning("paligo.fetch_error", url=page_url, error=str(exc))
            http.polite_sleep()
            continue
        fragment = _extract(body, final)
        if fragment is None:
            lost += 1
            log.warning("paligo.no_content", url=page_url)
            http.polite_sleep()
            continue
        stem = urlparse(page_url).path.rsplit("/", 1)[-1].removesuffix(".html")
        (raw_dir / f"{i:04d}-{stem}.html").write_text(
            f"<!-- source: {page_url} -->\n<section>\n{fragment}\n</section>\n", encoding="utf-8"
        )
        saved += 1
        http.polite_sleep()

    log.info("paligo.acquire", base=base, found=len(pages), pages=saved, slug=slug)
    return AcquireResult(
        raw_dir=raw_dir,
        kind="html",
        slug=slug,
        pages=saved,
        title=title,
        truncated=truncated,
        lost=lost,
    )
