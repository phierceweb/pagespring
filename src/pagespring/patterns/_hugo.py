"""Hugo acquisition for docs_probe — sitemap-driven crawl.

Hugo publishes a ``sitemap.xml`` at its *site* root, which on a multi-site host
is a subdirectory (``/<product>/<locale>/``) rather than the origin — so the
sitemap is discovered by walking up from the given URL. Keep only pages under
that URL's directory, so pointing at one product on a shared host doesn't drag
in its siblings. What one page holds is ``_hugo_page``'s concern.

The sitemap lists pages in no reading order, so they are staged in the order of
the theme's sidebar. A crawl over the page cap keeps the pages that come first in
the fullest sidebar on the entry page and the first few sitemap pages (a landing
page may have none). A list page is dropped once every page it lists is staged,
since it only repeats their excerpts. Hugo also publishes a ``/print/`` view
holding the whole site concatenated at the site root; including it would
duplicate every other page.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from pf_core.exceptions import ClientError, InvalidInputError
from pf_core.log import get_logger
from pf_core.utils.hashing import content_hash

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns._hugo_page import (
    extract,
    is_empty,
    is_whole_section_view,
    listed_pages,
    sidebar,
)
from pagespring.patterns._nav_order import page_key, reading_order
from pagespring.patterns._site import names_a_file, raw_stem

log = get_logger(__name__)

_MAX_PAGES = 6000
_SIDEBAR_PROBES = 5
_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_LOC = f"{_NS}loc"
_SITEMAP_EL = f"{_NS}sitemap"
# Docsy writes the page kind on <body> as td-<kind>.
_DOCSY_KINDS = frozenset({"td-home", "td-section", "td-page", "td-taxonomy", "td-term"})
# The print view, and the taxonomy list pages Hugo auto-generates: those index the
# manual rather than belonging to it, and their shell duplicates the home page.
_GENERATED_SEGS = ("print", "categories", "tags")


def is_docsy(html: str) -> bool:
    """Docsy's page shell: a ``td-<kind>`` body class around its ``td-main`` layout.

    A Docsy page may carry no generator tag; this tell routes it here all the same."""
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body
    if body is None or not _DOCSY_KINDS.intersection(body.get_attribute_list("class")):
        return False
    return soup.select_one(".td-main") is not None


def _is_content_page(url: str, base: str, roots: set[str]) -> bool:
    """True when ``url`` is a real topic under ``base`` — not a print or taxonomy
    page directly under one of the site ``roots``."""
    if url != base and not url.startswith(base + "/"):
        return False
    generated = [f"{root}/{seg}" for root in roots for seg in _GENERATED_SEGS]
    return not any(url == g or url.startswith(g + "/") for g in generated)


def _base_dir(url: str) -> str:
    """``url`` with any trailing file component dropped, no trailing slash."""
    p = urlparse(url)
    segs = [s for s in p.path.split("/") if s]
    if segs and names_a_file(segs[-1]):
        segs.pop()
    return f"{p.scheme}://{p.netloc}" + ("/" + "/".join(segs) if segs else "")


def _find_sitemap(base_dir: str) -> tuple[str, str]:
    """Walk up from ``base_dir`` to the first path serving a sitemap."""
    p = urlparse(base_dir)
    origin = f"{p.scheme}://{p.netloc}"
    segs = [s for s in p.path.split("/") if s]
    while True:
        url = "/".join([origin, *segs, "sitemap.xml"])
        try:
            _final, body = http.fetch_text(url)
        except Exception:
            body = None
        if body is not None and ("<urlset" in body or "<sitemapindex" in body):
            return url, body
        if not segs:
            raise InvalidInputError(
                f"no sitemap.xml found at or above {base_dir} — Hugo publishes one at its "
                "site root; the source may not be Hugo-built."
            )
        segs.pop()


def _page_locs(sitemap_url: str, sitemap: str) -> tuple[list[str], set[str], bool]:
    """Page URLs from a sitemap, the site roots (sitemap directories) it spans, and
    whether any child sitemap was unreadable.

    A multilingual Hugo site publishes an index whose ``<loc>``s are child
    *sitemaps*, one per language root, not pages — crawling those directly
    collects nothing. An unreadable child takes its whole page block with it,
    and those pages are never discovered, so only ``truncated`` can carry the loss.
    """
    try:
        root = ET.fromstring(sitemap)
    except ET.ParseError as exc:
        raise InvalidInputError(f"{sitemap_url} is not a valid sitemap") from exc
    roots = {sitemap_url.rsplit("/", 1)[0]}
    if root.find(_SITEMAP_EL) is None:
        return [el.text.strip() for el in root.iter(_LOC) if el.text], roots, False

    locs: list[str] = []
    child_failed = False
    for child in root.iter(_SITEMAP_EL):
        el = child.find(_LOC)
        if el is None or not el.text:
            continue
        child_url = el.text.strip()
        try:
            _f, body = http.fetch_text(child_url)
            locs.extend(x.text.strip() for x in ET.fromstring(body).iter(_LOC) if x.text)
            roots.add(child_url.rsplit("/", 1)[0])
        except (OSError, ET.ParseError, ClientError) as exc:
            child_failed = True
            log.warning("hugo.child_sitemap_error", url=child_url, error=str(exc))
        http.polite_sleep()
    return locs, roots, child_failed


def _fullest_sidebar(
    urls: list[str], keys: set[str], fetched: dict[str, tuple[str, str]]
) -> list[str]:
    """The sidebar on ``urls`` that lists the most of ``keys``; each page fetched is
    kept in ``fetched`` for the crawl."""
    best: list[str] = []
    for url in urls:
        if page_key(url) in fetched:
            continue
        try:
            final, body = http.fetch_text(url)
        except Exception as exc:
            log.warning("hugo.sidebar_probe_error", url=url, error=str(exc))
            continue
        finally:
            http.polite_sleep()
        fetched[page_key(url)] = (final, body)
        nav = sidebar(BeautifulSoup(body, "html.parser"), final)
        if len(keys.intersection(nav)) > len(keys.intersection(best)):
            best = nav
    return best


def acquire(base_url: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    base = _base_dir(base_url)
    sitemap_url, sitemap = _find_sitemap(base)
    locs, roots, child_failed = _page_locs(sitemap_url, sitemap)

    unique: dict[str, str] = {}
    for url in locs:
        if _is_content_page(url, base, roots | {base}):
            unique.setdefault(page_key(url), url)
    pages = list(unique.values())
    truncated = child_failed or len(pages) > _MAX_PAGES
    fetched: dict[str, tuple[str, str]] = {}
    if len(pages) > _MAX_PAGES:
        log.warning("hugo.capped", found=len(pages), cap=_MAX_PAGES)
        probes = [base_url, *pages[:_SIDEBAR_PROBES]]
        pages = reading_order(pages, _fullest_sidebar(probes, set(unique), fetched))[:_MAX_PAGES]
    keys = {page_key(u) for u in pages}

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    staged: dict[str, Path] = {}
    digests: set[str] = set()
    have: set[str] = set()
    listings: dict[str, set[str]] = {}
    order: list[str] = []
    listed = 0
    lost = 0
    for i, page in enumerate(pages):
        prefetched = fetched.pop(page_key(page), None)
        try:
            final, body = prefetched or http.fetch_text(page)
        except Exception as exc:
            lost += 1
            log.warning("hugo.fetch_error", url=page, error=str(exc))
            continue
        finally:
            http.polite_sleep()
        soup = BeautifulSoup(body, "html.parser")
        if is_whole_section_view(soup):
            log.info("hugo.whole_section_view", url=page)
            continue
        # Read before extract, which strips a sidebar nested in the container.
        nav = sidebar(soup, final)
        if (hits := len(keys.intersection(nav))) > listed:
            order, listed = nav, hits
        fragment = extract(soup, final)
        if fragment is None:
            lost += 1
            log.warning("hugo.no_main", url=page)
            continue
        if is_empty(fragment):
            log.info("hugo.empty_page", url=page)
            continue
        # A sitemap entry that redirects to another page is that page again.
        digest = content_hash(fragment)
        have.update((page_key(page), page_key(final)))
        if digest in digests:
            log.info("hugo.duplicate_page", url=page)
            continue
        digests.add(digest)
        crawled = raw_dir / f"crawl-{i:04d}.html"
        crawled.write_text(
            f"<!-- source: {page} -->\n<section>\n{fragment}\n</section>\n", encoding="utf-8"
        )
        staged[page] = crawled
        if entries := listed_pages(fragment, final):
            listings[page] = entries

    for page, entries in listings.items():
        if entries <= have:
            log.info("hugo.list_page", url=page, entries=len(entries))
            staged.pop(page).unlink()

    for n, page in enumerate(reading_order(list(staged), order)):
        stem = raw_stem(urlparse(page).path.removesuffix(".html"))
        staged[page].rename(raw_dir / f"{n:04d}-{stem}.html")

    log.info(
        "hugo.acquire",
        base=base,
        sitemap=sitemap_url,
        pages=len(staged),
        sidebar_pages=listed,
        slug=slug,
        truncated=truncated,
    )
    return AcquireResult(
        raw_dir=raw_dir,
        kind="html",
        slug=slug,
        pages=len(staged),
        title=title,
        truncated=truncated,
        lost=lost,
    )
