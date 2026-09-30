"""Sitemap reading, URL scope and the page fetch loop shared by the sitemap-driven
crawls (``sitemap_crawl`` and the Starlight strategy).

A sitemap index lists child sitemaps rather than pages, so it is expanded; page
order is the sitemap's own.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger
from pf_core.utils.hashing import content_hash

from pagespring import http
from pagespring.config import cfg
from pagespring.liveness import ProgressWatchdog
from pagespring.patterns._site import names_a_file, raw_stem

log = get_logger(__name__)

_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_ERROR_PAGES = {"404", "404.html"}


def read_locs(url: str) -> tuple[str, list[str], bool]:
    """(final URL, page URLs in sitemap order, whether a child sitemap was unreadable).

    An unreadable child takes its whole block of pages with it, so the caller
    must report the crawl truncated.

    Raises:
        InvalidInputError: ``url`` does not serve a sitemap.
    """
    final, body = http.fetch_text(url)
    root = _parse(final, body)
    if root.tag != f"{_NS}sitemapindex":
        return final, _locs(root, "url"), False
    locs: list[str] = []
    failed = False
    for child_url in _locs(root, "sitemap"):
        http.polite_sleep()
        try:
            child_final, child = http.fetch_text(child_url)
            locs.extend(_locs(_parse(child_final, child), "url"))
        except Exception as exc:
            failed = True
            log.warning("sitemap.child_error", url=child_url, error=str(exc))
    return final, locs, failed


def _parse(url: str, body: str) -> ET.Element:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise InvalidInputError(f"{url} is not a sitemap") from exc
    if root.tag not in (f"{_NS}urlset", f"{_NS}sitemapindex"):
        raise InvalidInputError(f"{url} is not a sitemap (root element {root.tag})")
    return root


def _locs(root: ET.Element, entry: str) -> list[str]:
    """The ``<loc>`` of each ``<url>``/``<sitemap>`` entry — never an alternate's href."""
    out: list[str] = []
    for el in root.iter(f"{_NS}{entry}"):
        loc = el.find(f"{_NS}loc")
        if loc is not None and loc.text and loc.text.strip():
            out.append(loc.text.strip())
    return out


def sitemap_link(html: str, page_url: str) -> str | None:
    """The sitemap a page declares with ``<link rel="sitemap">``, absolute, or None."""
    link = BeautifulSoup(html, "html.parser").find("link", rel="sitemap")
    href = link.get("href") if isinstance(link, Tag) else None
    return urljoin(page_url, href) if isinstance(href, str) and href.strip() else None


def under(url: str, base: str) -> bool:
    """``url`` is ``base`` or below it: a path-segment prefix on the same host."""
    u, b = urlparse(url), urlparse(base)
    if u.netloc.lower() != b.netloc.lower():
        return False
    path = u.path if u.path.endswith("/") else f"{u.path}/"
    prefix = b.path if b.path.endswith("/") else f"{b.path}/"
    return path.startswith(prefix)


def directory(url: str) -> str:
    """``url``'s directory, with a trailing slash; a trailing file name is dropped."""
    p = urlparse(url)
    segs = [s for s in p.path.split("/") if s]
    if segs and names_a_file(segs[-1]):
        segs.pop()
    return f"{p.scheme}://{p.netloc}/" + "".join(f"{s}/" for s in segs)


def page_key(url: str) -> tuple[str, str]:
    """Identity of a page URL: a trailing slash, query or fragment names the same page."""
    p = urlparse(url)
    return p.netloc.lower(), p.path.rstrip("/")


def unique(urls: list[str]) -> list[str]:
    """``urls`` without repeats of the same page, first occurrence kept."""
    seen: set[tuple[str, str]] = set()
    out: list[str] = []
    for url in urls:
        key = page_key(url)
        if key not in seen:
            seen.add(key)
            out.append(url)
    return out


def is_error_page(url: str) -> bool:
    """A generator's not-found page, which some sitemaps list like any other."""
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1].lower() in _ERROR_PAGES


@dataclass
class Crawl:
    """What ``crawl`` staged, lost, and whether it stopped for lack of progress."""

    saved: int = 0
    lost: int = 0
    stalled: bool = False
    first_page: str | None = None  # the first staged page's HTML, for site metadata


def crawl(
    urls: list[str],
    raw_dir: Path,
    *,
    extract: Callable[[str, str], str | None],
    in_scope: Callable[[str], bool],
    belongs: Callable[[str], bool] | None = None,
    event: str,
) -> Crawl:
    """Fetch ``urls`` in order and stage each page's fragment as ``raw/NNNN-<path>.html``.

    A fetch error or a page ``extract`` finds no content in counts as lost. A page
    that redirects out of scope, one ``belongs`` rejects (the site's generator did
    not build it), and a repeat of staged content are skipped.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    result = Crawl()
    hashes: set[str] = set()
    watchdog = ProgressWatchdog(stall_after_s=cfg.CRAWL_STALL_AFTER_S, now=time.monotonic)
    for i, url in enumerate(urls):
        if watchdog.stalled():
            log.warning(
                f"{event}.stalled",
                saved=result.saved,
                idle_s=round(watchdog.idle_s()),
                queued=len(urls) - i,
            )
            result.stalled = True
            break
        http.polite_sleep()
        try:
            final, body = http.fetch_text(url)
        except Exception as exc:
            result.lost += 1
            log.warning(f"{event}.fetch_error", url=url, error=str(exc))
            continue
        if not in_scope(final):
            log.info(f"{event}.left_scope", url=url, final=final)
            continue
        if belongs is not None and not belongs(body):
            log.info(f"{event}.foreign_page", url=final)
            continue
        fragment = extract(body, final)
        if fragment is None:
            result.lost += 1
            log.warning(f"{event}.no_content", url=final)
            continue
        digest = content_hash(fragment)
        if digest in hashes:
            continue
        hashes.add(digest)
        stem = raw_stem(urlparse(final).path)
        (raw_dir / f"{result.saved:04d}-{stem}.html").write_text(
            f"<!-- source: {final} -->\n<section>\n{fragment}\n</section>\n", encoding="utf-8"
        )
        if result.first_page is None:
            result.first_page = body
        result.saved += 1
        watchdog.progress()
    return result
