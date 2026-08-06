"""Asciidoctor acquisition for docs_probe — same-directory crawl.

Asciidoctor's multi-page output splits a book into sibling ``.html`` files that
all carry the same nav, so the page set is reachable from any one of them.

A page carrying neither content-container id is an error, not a guess — a
speculative selector ladder stages a hollow deliverable when a theme changes.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger
from pf_core.utils.slugify import slugify

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.config import cfg
from pagespring.liveness import ProgressWatchdog
from pagespring.patterns._site import (
    absolutize_refs,
    flatten_responsive_images,
    names_a_file,
    page_title,
    strip_scripts,
)

log = get_logger(__name__)

_MAX_PAGES = 2000
# Ordered, most-specific first: a renamed container leaves the stock id on an
# outer wrapper holding the search chrome, which a grouped selector would take.
_CONTENT_IDS = ("contentOrg", "content")


def _base_dir(url: str) -> str:
    """The directory the sibling pages live in.

    Operates on the path: a host root has nothing to strip, and a directory-form
    URL keeps its last segment."""
    p = urlparse(url.split("?", 1)[0].split("#", 1)[0])
    path = p.path
    if path.endswith("/"):
        dir_path = path.rstrip("/")
    elif names_a_file(path.rsplit("/", 1)[-1]):
        dir_path = path.rsplit("/", 1)[0]
    else:
        dir_path = path
    return f"{p.scheme}://{p.netloc}{dir_path}"


def _normalize_seed(url: str) -> str:
    """Give a directory seed its trailing slash back.

    ``docs_probe`` hands over ``url.rstrip("/")``, and this is the urljoin base
    for links and assets — without the slash they resolve into the parent."""
    p = urlparse(url.split("#", 1)[0])
    if names_a_file(p.path.rstrip("/").rsplit("/", 1)[-1]):
        return urlunparse(p)
    return urlunparse(p._replace(path=p.path.rstrip("/") + "/"))


def _same_dir_links(html: str, page_url: str, base_dir: str) -> list[str]:
    """Sibling ``.html`` URLs in document order — the author's reading order.

    Scoped to ``base_dir`` so the nav's parent-directory language switcher and
    any offsite link stay out of the crawl.
    """
    out: list[str] = []
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not isinstance(href, str) or href.startswith(("#", "mailto:", "data:")):
            continue
        absolute = urljoin(page_url, href).split("#", 1)[0]
        if not urlparse(absolute).path.lower().endswith(".html"):
            continue
        if _base_dir(absolute) == base_dir:
            out.append(absolute)
    return out


def _extract(page_html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(page_html, "html.parser")
    node = next(
        (n for n in (soup.find(id=cid) for cid in _CONTENT_IDS) if isinstance(n, Tag)), None
    )
    if node is None:
        return None
    strip_scripts(node)
    flatten_responsive_images(node)
    absolutize_refs(node, page_url)
    return str(node)


def acquire(url: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    seed = _normalize_seed(url)
    # base_dir, link resolution and asset refs all anchor here, so this must be
    # the post-redirect URL.
    entry_url, entry = http.fetch_text(seed)
    base_dir = _base_dir(entry_url)

    doc_title = title or page_title(entry)
    if doc_title:
        slug = slugify(f"{slug}-{doc_title}") or slug

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    queue: list[str] = [entry_url]
    seen: set[str] = {entry_url}
    bodies: dict[str, str] = {entry_url: entry}
    staged_urls: set[str] = set()
    staged_hashes: set[str] = set()
    saved = 0
    lost = 0
    # Siblings that exist: extracted, or failed for a reason other than "absent".
    # A cross-reference to a doc not published on this host is not a missing page.
    live_siblings = 0
    watchdog = ProgressWatchdog(stall_after_s=cfg.CRAWL_STALL_AFTER_S, now=time.monotonic)
    while queue and saved < _MAX_PAGES:
        if watchdog.stalled():
            log.warning(
                "asciidoctor.stalled",
                saved=saved,
                idle_s=round(watchdog.idle_s()),
                queued=len(queue),
            )
            break
        page_url = queue.pop(0)
        body = bodies.pop(page_url, None)
        if body is None:
            try:
                page_url, body = http.fetch_text(page_url)
            except Exception as exc:
                if not (isinstance(exc, HTTPError) and exc.code == 404):
                    live_siblings += 1
                    lost += 1
                log.warning("asciidoctor.fetch_error", url=page_url, error=str(exc))
                http.polite_sleep()
                continue
            # Scope was checked on the href; a redirect can still leave the manual.
            if _base_dir(page_url) != base_dir:
                log.warning("asciidoctor.left_base_dir", url=page_url, base=base_dir)
                http.polite_sleep()
                continue
        if page_url in staged_urls:
            http.polite_sleep()
            continue
        for link in _same_dir_links(body, page_url, base_dir):
            if link not in seen:
                seen.add(link)
                queue.append(link)
        fragment = _extract(body, page_url)
        if fragment is None:
            if page_url != entry_url:
                live_siblings += 1
            lost += 1
            log.warning("asciidoctor.no_content", url=page_url)
            http.polite_sleep()
            continue
        # A directory URL and its index.html are one page under two names, as are
        # redirect aliases; identical content is the only reliable tell.
        digest = hashlib.sha256(fragment.encode("utf-8")).hexdigest()
        if digest in staged_hashes:
            http.polite_sleep()
            continue
        if page_url != entry_url:
            live_siblings += 1
        staged_urls.add(page_url)
        staged_hashes.add(digest)
        stem = urlparse(page_url).path.rsplit("/", 1)[-1].removesuffix(".html") or "index"
        (raw_dir / f"{saved:04d}-{stem}.html").write_text(
            f"<!-- source: {page_url} -->\n<section>\n{fragment}\n</section>\n", encoding="utf-8"
        )
        saved += 1
        watchdog.progress()
        http.polite_sleep()

    if saved == 0:
        raise InvalidInputError(
            f"{entry_url} declares Asciidoctor but no page under {base_dir} exposes a "
            f"{' / '.join('#' + i for i in _CONTENT_IDS)} container — the publisher's "
            "template renamed it, and guessing at the right node would stage a "
            "hollow deliverable."
        )

    truncated = bool(queue)
    if truncated:
        log.warning("asciidoctor.capped", saved=saved, cap=_MAX_PAGES, queued=len(queue))

    # One file is Asciidoctor's default output. Only a sibling that actually
    # exists means this entry was a chapter of something larger.
    single = saved == 1 and live_siblings == 0

    log.info("asciidoctor.acquire", base=base_dir, pages=saved, slug=slug, single=single)
    return AcquireResult(
        raw_dir=raw_dir,
        kind="html",
        slug=slug,
        pages=saved,
        title=doc_title,
        truncated=truncated,
        single_document=single,
        lost=lost,
    )
