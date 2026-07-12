"""Sphinx acquisition for docs_probe — same-prefix BFS crawl.

Sphinx exposes no machine index that works generically across themes
(RTD-hosted projects route to the readthedocs pattern and its PDF build
instead), so crawl: BFS same-host links under the start URL's directory
prefix, extract the ``div[role=main]`` content root (fallbacks: ``div.body``,
``main``), strip headerlink anchors, absolutize refs. Capped; a capped crawl
warns — a silently truncated crawl reads as a complete one.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns._site import absolutize_refs

log = get_logger(__name__)

_MAX_PAGES = 1000
# Sphinx utility trees (whole path segments) and utility pages (filename stems)
# that a same-prefix crawl must skip.
_SKIP_DIRS = {"_static", "_sources", "_modules", "_images", "_downloads"}
_SKIP_PAGES = {"genindex", "genindex-all", "search", "py-modindex"}


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


def _extract(html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    root = (
        soup.find(True, attrs={"role": "main"})
        or soup.find("div", class_="body")
        or soup.find("main")
    )
    if not isinstance(root, Tag):
        return None
    for el in root.select("a.headerlink"):
        el.decompose()
    absolutize_refs(root, page_url)
    return str(root)


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
    saved = 0
    while queue and saved < _MAX_PAGES:
        url = queue.popleft()
        try:
            final, body = http.fetch_text(url)
        except Exception as exc:
            log.warning("sphinx.fetch_error", url=url, error=str(exc))
            http.polite_sleep()
            continue
        fragment = _extract(body, final)
        if fragment is not None:
            stem = urlparse(url).path[len(prefix) :].strip("/").replace("/", "-") or "index"
            (raw_dir / f"{saved:04d}-{stem}.html").write_text(
                f"<!-- source: {url} -->\n<section>\n{fragment}\n</section>\n",
                encoding="utf-8",
            )
            saved += 1
        else:
            log.warning("sphinx.no_content_root", url=url)
        for a in BeautifulSoup(body, "html.parser").find_all("a"):
            href = a.get("href")
            if not isinstance(href, str) or not href:
                continue
            nxt = urldefrag(urljoin(final, href)).url
            if nxt not in seen and _wanted(nxt, host, prefix):
                seen.add(nxt)
                queue.append(nxt)
        http.polite_sleep()
    if queue:
        log.warning("sphinx.capped", saved=saved, cap=_MAX_PAGES, queued=len(queue))
    log.info("sphinx.acquire", base=base, pages=saved, slug=slug)
    return AcquireResult(raw_dir=raw_dir, kind="html", slug=slug, pages=saved, title=title)
