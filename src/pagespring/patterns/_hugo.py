"""Hugo acquisition for docs_probe — sitemap-driven crawl.

Hugo publishes a ``sitemap.xml`` at its *site* root, which on a multi-site host
is a subdirectory (``/<product>/<locale>/``) rather than the origin — so the
sitemap is discovered by walking up from the given URL. Keep only pages under
that URL's directory, so pointing at one product on a shared host doesn't drag
in its siblings. Content lives in ``<main>`` across the Hugo docs themes.

Hugo also publishes a ``/print/`` view holding the whole site concatenated;
including it would duplicate every other page.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
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

_MAX_PAGES = 6000
_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_LOC = f"{_NS}loc"
_SITEMAP_EL = f"{_NS}sitemap"
# Doc themes render the whole chapter list into every page, and most do it in a
# plain <div> rather than a <nav>. These are the containers the shipped themes
# use; an unlisted theme's sidebar simply survives, which is the safe failure.
_CHROME_CSS = (
    "nav, header, footer, div.drawer, div.book-menu, div.td-sidebar, aside.sidebar, #sidebar"
)
_PRINT_SEG = "/print/"
# Hugo auto-generates taxonomy list pages. They index the manual rather than
# belonging to it, and their list-page shell duplicates the home page verbatim.
_TAXONOMY_SEGS = ("/categories/", "/tags/")


def _is_content_page(url: str, base: str) -> bool:
    """True when ``url`` is a real topic under ``base`` — not print or taxonomy."""
    if url != base and not url.startswith(base + "/"):
        return False
    path = urlparse(url).path
    if not path.endswith("/"):
        path += "/"
    return _PRINT_SEG not in path and not any(seg in path for seg in _TAXONOMY_SEGS)


def _base_dir(url: str) -> str:
    """``url`` with any trailing file component dropped, no trailing slash."""
    p = urlparse(url)
    segs = [s for s in p.path.split("/") if s]
    if segs and "." in segs[-1]:
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


def _extract(page_html: str, page_url: str) -> str | None:
    """The page's ``<main>`` as a cleaned, absolutized fragment (None if absent)."""
    soup = BeautifulSoup(page_html, "html.parser")
    main = soup.find("main")
    if not isinstance(main, Tag):
        return None
    for el in main.select(_CHROME_CSS):
        el.decompose()
    strip_scripts(main)
    flatten_responsive_images(main)
    absolutize_refs(main, page_url)
    return str(main)


def _page_locs(sitemap_url: str, sitemap: str) -> tuple[list[str], bool]:
    """Page URLs from a sitemap, and whether any child sitemap was unreadable.

    A multilingual Hugo site publishes an index whose ``<loc>``s are child
    *sitemaps*, not pages — crawling those directly collects nothing. An
    unreadable child takes its whole page block with it, and those pages are
    never discovered, so only ``truncated`` can carry the loss.
    """
    try:
        root = ET.fromstring(sitemap)
    except ET.ParseError as exc:
        raise InvalidInputError(f"{sitemap_url} is not a valid sitemap") from exc
    if root.find(_SITEMAP_EL) is None:
        return [el.text.strip() for el in root.iter(_LOC) if el.text], False

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
        except (OSError, ET.ParseError) as exc:
            child_failed = True
            log.warning("hugo.child_sitemap_error", url=child_url, error=str(exc))
        http.polite_sleep()
    return locs, child_failed


def acquire(base_url: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    base = _base_dir(base_url)
    sitemap_url, sitemap = _find_sitemap(base)
    locs, child_failed = _page_locs(sitemap_url, sitemap)

    pages = [u for u in locs if _is_content_page(u, base)]
    truncated = child_failed or len(pages) > _MAX_PAGES
    if len(pages) > _MAX_PAGES:
        log.warning("hugo.capped", found=len(pages), cap=_MAX_PAGES)
        pages = pages[:_MAX_PAGES]

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    lost = 0
    for i, page in enumerate(pages):
        try:
            final, body = http.fetch_text(page)
        except Exception as exc:
            lost += 1
            log.warning("hugo.fetch_error", url=page, error=str(exc))
            http.polite_sleep()
            continue
        fragment = _extract(body, final)
        if fragment is None:
            lost += 1
            log.warning("hugo.no_main", url=page)
            http.polite_sleep()
            continue
        stem = urlparse(page).path.strip("/").replace("/", "-") or "index"
        (raw_dir / f"{i:04d}-{stem}.html").write_text(
            f"<!-- source: {page} -->\n<section>\n{fragment}\n</section>\n", encoding="utf-8"
        )
        saved += 1
        http.polite_sleep()

    log.info(
        "hugo.acquire",
        base=base,
        sitemap=sitemap_url,
        pages=saved,
        slug=slug,
        truncated=truncated,
    )
    return AcquireResult(
        raw_dir=raw_dir,
        kind="html",
        slug=slug,
        pages=saved,
        title=title,
        truncated=truncated,
        lost=lost,
    )
