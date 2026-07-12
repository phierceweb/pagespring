"""Docusaurus acquisition for docs_probe — sitemap-driven crawl.

Docusaurus server-renders page content into ``<article>`` and publishes a
standard ``sitemap.xml``. Keep only URLs under the base path the user gave
(pointing at ``/docs`` selects the docs, not the blog) and drop versioned
siblings (``/docs/2.4.1/…``, ``/docs/next/…``) so exactly the current docs
land. Per page: extract ``<article>``, drop nav chrome, absolutize refs.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns._site import absolutize_refs

log = get_logger(__name__)

_MAX_PAGES = 1000
# Versioned-doc dir names: "2.4.1", "3.0.1-rc", "2.x", "next".
_VERSION_SEG_RE = re.compile(r"^(?:\d+\.(?:\d+|x)[^/]*|next)$")
_LOC = "{http://www.sitemaps.org/schemas/sitemap/0.9}loc"
# Docusaurus in-article chrome: breadcrumbs / pagination navs, edit links, mobile TOC.
_CHROME_CSS = "nav, a.theme-edit-this-page, div.theme-doc-toc-mobile"


def _keep(url: str, base: str) -> bool:
    """True for pages under base that are not versioned-doc siblings."""
    if url != base and not url.startswith(base + "/"):
        return False
    first = url[len(base) :].strip("/").split("/")[0]
    return not _VERSION_SEG_RE.match(first)


def _extract(html: str, page_url: str) -> str | None:
    """The page's <article> as a cleaned, absolutized fragment (None if absent)."""
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article")
    if not isinstance(article, Tag):
        return None
    for el in article.select(_CHROME_CSS):
        el.decompose()
    absolutize_refs(article, page_url)
    return str(article)


def acquire(base_url: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    base = base_url.rstrip("/")
    p = urlparse(base)
    origin = f"{p.scheme}://{p.netloc}"
    _final, sm = http.fetch_text(f"{origin}/sitemap.xml")
    try:
        locs = [el.text.strip() for el in ET.fromstring(sm).iter(_LOC) if el.text]
    except ET.ParseError as exc:
        raise InvalidInputError(f"{origin}/sitemap.xml is not a valid sitemap") from exc
    urls = [u for u in locs if _keep(u, base)]
    if len(urls) > _MAX_PAGES:
        log.warning("docusaurus.truncated", found=len(urls), cap=_MAX_PAGES)
        urls = urls[:_MAX_PAGES]

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    for i, url in enumerate(urls):
        try:
            final, body = http.fetch_text(url)
        except Exception as exc:
            log.warning("docusaurus.fetch_error", url=url, error=str(exc))
            http.polite_sleep()
            continue
        fragment = _extract(body, final)
        if fragment is None:
            log.warning("docusaurus.no_article", url=url)
            http.polite_sleep()
            continue
        stem = urlparse(url).path.strip("/").replace("/", "-") or "index"
        (raw_dir / f"{i:04d}-{stem}.html").write_text(
            f"<!-- source: {url} -->\n<section>\n{fragment}\n</section>\n", encoding="utf-8"
        )
        saved += 1
        http.polite_sleep()
    log.info("docusaurus.acquire", base=base, pages=saved, slug=slug)
    return AcquireResult(raw_dir=raw_dir, kind="html", slug=slug, pages=saved, title=title)
