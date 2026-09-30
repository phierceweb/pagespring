"""Sphinx acquisition — same-prefix breadth-first crawl.

Sphinx exposes no machine index that works generically across themes (the
readthedocs pattern takes an RTD project's PDF build first), so crawl:
breadth-first same-host links under the start URL's directory prefix, extract
the ``div[role=main]`` content root (fallbacks: ``div.body``, ``main``), strip
headerlink anchors, absolutize refs, and stage pages in the toctree order themes
render into each page. Capped; a capped crawl warns — a silently truncated crawl
reads as a complete one.
"""

from __future__ import annotations

import re
import time
from collections import deque
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from pf_core.log import get_logger
from pf_core.utils.hashing import content_hash

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.config import cfg
from pagespring.liveness import ProgressWatchdog
from pagespring.patterns._site import (
    absolutize_refs,
    flatten_responsive_images,
    generator_meta,
    strip_scripts,
)
from pagespring.patterns._sphinx_order import Siblings, reading_order

log = get_logger(__name__)

# Sphinx's OWN assets, shipped by the `basic` theme every stock theme inherits.
# A bare "_static/" is any site's asset directory and claims sites that are not
# Sphinx at all — and _extract's ladder ends at <main>, so the mis-route succeeds.
_TELLS = ("_static/documentation_options.js", "_static/doctools.js", "_static/pygments.css")

_MAX_PAGES = 1000
# Sphinx utility trees (whole path segments) and utility pages (filename stems)
# that a same-prefix crawl must skip.
_SKIP_DIRS = {"_static", "_sources", "_modules", "_images", "_downloads"}
_SKIP_PAGES = {"genindex", "genindex-all", "search", "py-modindex"}
_TOC_ITEM = re.compile(r"^toctree-l\d+$")


def is_sphinx(html: str) -> bool:
    """Generator meta, or one of Sphinx's own ``_static/`` assets.

    The docutils-plus-``_static/`` conjunction is the backstop for a theme that
    ships none of the named assets."""
    gen = generator_meta(html)
    if "sphinx" in gen:
        return True
    if any(tell in html for tell in _TELLS):
        return True
    return "docutils" in gen and "_static/" in html


def _prefix(base: str) -> str:
    """The crawl's directory prefix: base's path up to its last '/'."""
    path = urlparse(base).path
    return path if path.endswith("/") else path.rsplit("/", 1)[0] + "/"


def _wanted(url: str, host: str, prefix: str) -> bool:
    p = urlparse(url)
    if p.netloc.lower() != host or not p.path.startswith(prefix):
        return False
    if any(seg in _SKIP_DIRS for seg in p.path.split("/")):
        return False
    last = p.path.rstrip("/").rsplit("/", 1)[-1]
    if last.split(".")[0] in _SKIP_PAGES:
        return False
    return p.path.endswith("/") or last.endswith(".html") or "." not in last


def _content_root(soup: BeautifulSoup) -> Tag | None:
    root = (
        soup.find(True, attrs={"role": "main"})
        or soup.find("div", class_="body")
        or soup.find("main")
    )
    return root if isinstance(root, Tag) else None


def _extract(html: str, page_url: str) -> str | None:
    root = _content_root(BeautifulSoup(html, "html.parser"))
    if root is None:
        return None
    for el in root.select("a.headerlink"):
        el.decompose()
    strip_scripts(root)
    flatten_responsive_images(root)
    absolutize_refs(root, page_url)
    return str(root)


def _entry_url(li: Tag, page_url: str) -> str | None:
    a = li.find("a", href=True)
    return urldefrag(urljoin(page_url, str(a["href"]))).url if isinstance(a, Tag) else None


def _toc_siblings(soup: BeautifulSoup, page_url: str) -> list[Siblings]:
    """The page's toctree sibling lists. A nested list's parent is its enclosing
    entry, and a content-root toctree's is the page; a sidebar's top level is None
    — themes root it at the site or at the current section."""
    root = _content_root(soup)
    lists: dict[int, tuple[str | None, list[str]]] = {}
    for li in soup.find_all("li", class_=_TOC_ITEM):
        url = _entry_url(li, page_url)
        if url is None:
            continue
        outer = li.find_parent("li", class_=_TOC_ITEM)
        if outer is not None:
            key, parent = id(outer), _entry_url(outer, page_url)
        elif root is not None and any(p is root for p in li.parents):
            key, parent = id(root), page_url
        else:
            key, parent = id(li.parent.parent if li.parent else li), None
        lists.setdefault(key, (parent, []))[1].append(url)
    return [(parent, tuple(urls)) for parent, urls in lists.values()]


def acquire(base_url: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    last = urlparse(base_url).path.rsplit("/", 1)[-1]
    # Only an .html-suffixed start URL (…/index.html) is a file to strip —
    # other dotted last segments are version dirs (/3.11, /en/5.0).
    base = (
        base_url.rsplit("/", 1)[0] + "/" if last.endswith(".html") else base_url.rstrip("/") + "/"
    )
    host = urlparse(base).netloc.lower()
    prefix = _prefix(base)

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    seen: set[str] = {base}
    queue: deque[str] = deque([base])
    found_on: dict[str, str] = {}
    pages: dict[str, str] = {}  # staged URL -> raw file body, in crawl order
    by_digest: dict[str, str] = {}
    alias: dict[str, str] = {}
    tocs: dict[Siblings, str | None] = {}
    lost = 0
    watchdog = ProgressWatchdog(stall_after_s=cfg.CRAWL_STALL_AFTER_S, now=time.monotonic)
    # Breadth-first, so a cap keeps the manual's upper levels rather than one
    # cross-reference chain; reading_order sets the staged order.
    while queue and len(pages) < _MAX_PAGES:
        if watchdog.stalled():
            idle = round(watchdog.idle_s())
            log.warning("sphinx.stalled", saved=len(pages), idle_s=idle, queued=len(queue))
            break
        url = queue.popleft()
        try:
            final, body = http.fetch_text(url)
        except Exception as exc:
            lost += 1
            log.warning("sphinx.fetch_error", url=url, error=str(exc))
            http.polite_sleep()
            continue
        fragment = _extract(body, final)
        if fragment is not None:
            # A directory URL and its index.html are one page under two names, as
            # are redirect aliases; identical content is the only reliable tell.
            digest = content_hash(fragment)
            if digest in by_digest:
                log.info("sphinx.duplicate_page", url=url)
            else:
                by_digest[digest] = url
                pages[url] = f"<!-- source: {url} -->\n<section>\n{fragment}\n</section>\n"
                watchdog.progress()
            alias[url] = by_digest[digest]
            alias.setdefault(final, by_digest[digest])
        else:
            lost += 1
            log.warning("sphinx.no_content_root", url=url)
        soup = BeautifulSoup(body, "html.parser")
        for sib in _toc_siblings(soup, final):
            if tocs.get(sib) is None:
                tocs[sib] = None if url in sib[1] or final in sib[1] else final
        found: list[str] = []
        for a in soup.find_all("a"):
            href = a.get("href")
            if not isinstance(href, str) or not href:
                continue
            nxt = urldefrag(urljoin(final, href)).url
            if nxt not in seen and _wanted(nxt, host, prefix):
                seen.add(nxt)
                found_on[nxt] = url
                found.append(nxt)
        queue.extend(found)
        http.polite_sleep()
    if queue:
        log.warning("sphinx.capped", saved=len(pages), cap=_MAX_PAGES, queued=len(queue))
    truncated = bool(queue)
    for i, url in enumerate(reading_order(list(pages), alias, found_on, tocs)):
        stem = urlparse(url).path[len(prefix) :].strip("/").replace("/", "-") or "index"
        (raw_dir / f"{i:04d}-{stem}.html").write_text(pages[url], encoding="utf-8")
    log.info("sphinx.acquire", base=base, pages=len(pages), slug=slug)
    return AcquireResult(
        raw_dir=raw_dir,
        kind="html",
        slug=slug,
        pages=len(pages),
        title=title,
        truncated=truncated,
        lost=lost,
    )
