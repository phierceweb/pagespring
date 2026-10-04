"""Starlight for docs_probe: the head-declared sitemap's pages in sidebar order, unlisted ones
beside their nearest relative, scoped to the seed's locale and directory."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns import _readable, _sitemap
from pagespring.patterns._site import names_a_file

log = get_logger(__name__)

_MAX_PAGES = 2000
_GENERATOR_RE = re.compile(r"<meta\b[^>]*>", re.I)
_STARLIGHT_CONTENT_RE = re.compile(r"""\bcontent\s*=\s*["']\s*starlight\b""", re.I)
_BODY_CLASS_RE = re.compile(r"""\bclass\s*=\s*["'][^"']*\bsl-markdown-content\b""", re.I)
_BODY_CSS = ".sl-markdown-content"
# Ordered: the sidebar pane, then the nav that frames it.
_SIDEBAR_CSS = ("#starlight__sidebar", "nav.sidebar")
_IN_BODY_CHROME_CSS = "a.sl-anchor-link"


def is_starlight(html: str) -> bool:
    """Generator meta, or the markdown body class ``acquire`` extracts, which a site overriding
    Starlight's ``Head`` keeps when it loses the generator tag."""
    for tag in _GENERATOR_RE.findall(html):
        if "generator" in tag.lower() and _STARLIGHT_CONTENT_RE.search(tag):
            return True
    return bool(_BODY_CLASS_RE.search(html))


def _seed(url: str) -> str:
    """``url`` with a directory's trailing slash restored — it anchors relative refs."""
    p = urlparse(url.split("#", 1)[0])
    if names_a_file(p.path.rstrip("/").rsplit("/", 1)[-1]):
        return urlunparse(p)
    return urlunparse(p._replace(path=p.path.rstrip("/") + "/"))


def _segs(url: str) -> tuple[str, ...]:
    return tuple(s for s in urlparse(url).path.split("/") if s)


def _root_url(url: str, segs: tuple[str, ...]) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/" + "".join(f"{s}/" for s in segs)


def _locale_roots(soup: Tag, page_url: str) -> tuple[tuple[str, ...] | None, set[tuple[str, ...]]]:
    """(this page's locale root, every locale root) as lowercased segments: each ``hreflang``
    alternate is this path under another root, so the segments they don't share are the roots."""
    own_segs = _segs(page_url)
    host = urlparse(page_url).netloc.lower()
    own: tuple[str, ...] | None = None
    roots: set[tuple[str, ...]] = set()
    for link in soup.find_all("link", rel="alternate", hreflang=True):
        href = link.get("href")
        if not isinstance(href, str) or link.get("hreflang") == "x-default":
            continue
        alt = urljoin(page_url, href)
        alt_segs = _segs(alt)
        if urlparse(alt).netloc.lower() != host or alt_segs == own_segs:
            continue
        shared = 0
        while (
            shared < min(len(own_segs), len(alt_segs))
            and own_segs[-1 - shared] == alt_segs[-1 - shared]
        ):
            shared += 1
        roots.add(tuple(s.lower() for s in alt_segs[: len(alt_segs) - shared]))
        if own is None:
            own = tuple(s.lower() for s in own_segs[: len(own_segs) - shared])
    if own is not None:
        roots.add(own)
    return own, roots


def _in_locale(url: str, own: tuple[str, ...] | None, roots: set[tuple[str, ...]]) -> bool:
    """The deepest locale root holding ``url`` is ``own``. Compared case-folded: a
    legacy ``/zh-CN/`` tree is the ``/zh-cn/`` locale."""
    if own is None:
        return True
    segs = tuple(s.lower() for s in _segs(url))
    holding = [r for r in roots if segs[: len(r)] == r]
    return max(holding, key=len, default=None) == own


def _sidebar(soup: Tag, page_url: str) -> tuple[list[str], set[tuple[str, str]] | None]:
    """The sidebar's page links in order, and the keys of those adopting unlisted pages: None when
    all do, else group links only (a top-level link beside groups is site navigation)."""
    for css in _SIDEBAR_CSS:
        nav = soup.select_one(css)
        if isinstance(nav, Tag):
            break
    else:
        return [], None
    links: list[str] = []
    grouped: set[tuple[str, str]] = set()
    for a in nav.find_all("a", href=True):
        href = a["href"]
        if not isinstance(href, str) or href.startswith("#"):
            continue
        url = urljoin(page_url, href).split("#", 1)[0].split("?", 1)[0]
        if urlparse(url).scheme in ("http", "https"):
            links.append(url)
            if a.find_parent("details") is not None:
                grouped.add(_sitemap.page_key(url))
    return _sitemap.unique(links), grouped if nav.find("details") is not None else None


def _reading_order(
    pages: list[str], sidebar: list[str], adopters: set[tuple[str, str]] | None = None
) -> list[str]:
    """``pages`` in sidebar order, unlisted ones beside their closest listed relatives (a section
    index first); with ``adopters`` set, a page sharing no section with one is dropped."""
    rank = {_sitemap.page_key(u): i for i, u in enumerate(sidebar)}
    listed = sorted(
        (p for p in pages if _sitemap.page_key(p) in rank),
        key=lambda p: rank[_sitemap.page_key(p)],
    )
    listed_segs = [_segs(p) for p in listed]
    before: defaultdict[int, list[str]] = defaultdict(list)
    after: defaultdict[int, list[tuple[int, int, str]]] = defaultdict(list)
    tail: list[str] = []
    unlisted = [p for p in pages if _sitemap.page_key(p) not in rank]
    for seq, page in enumerate(unlisted):
        segs = _segs(page)
        below = [i for i, s in enumerate(listed_segs) if s[: len(segs)] == segs]
        if below:
            before[below[0]].append(page)
            continue
        for depth in range(len(segs) - 1, -1, -1):
            if depth == 0 and adopters is not None:
                break
            kin = [
                i
                for i, s in enumerate(listed_segs)
                if s[:depth] == segs[:depth]
                and (adopters is None or _sitemap.page_key(listed[i]) in adopters)
            ]
            if kin:
                after[kin[-1]].append((-depth, seq, page))
                break
        else:
            tail.append(page)
    ordered: list[str] = []
    for i, page in enumerate(listed):
        ordered.extend(before[i])
        ordered.append(page)
        ordered.extend(p for _depth, _seq, p in sorted(after[i]))
    return ordered + tail


def _heading(main: Tag) -> list[Tag]:
    """The page's title ``h1`` — and on a splash page, the tagline under it."""
    h1 = next(
        (h for h in main.find_all("h1") if h.find_parent(class_="sl-markdown-content") is None),
        None,
    )
    if not isinstance(h1, Tag):
        return []
    tagline = h1.find_next_sibling()
    if (
        h1.find_parent(class_="hero") is not None
        and isinstance(tagline, Tag)
        and (tagline.name == "p" or "tagline" in (tagline.get("class") or []))
    ):
        return [h1, tagline]
    return [h1]


def _extract(html: str, page_url: str) -> str | None:
    """The title and markdown body as one cleaned fragment, None without a body; everything else in
    ``<main>`` (banners, footers, sponsor blocks a site adds) is frame."""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main")
    if not isinstance(main, Tag):
        return None
    bodies = [
        b for b in main.select(_BODY_CSS) if b.find_parent(class_="sl-markdown-content") is None
    ]
    if not bodies:
        return None
    page = soup.new_tag("article")
    for el in [*_heading(main), *bodies]:
        page.append(el.extract())
    for el in page.select(_IN_BODY_CHROME_CSS):
        el.decompose()
    _readable.clean(page, page_url)
    return str(page)


def acquire(base_url: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    # Scope anchors on the post-redirect URL: a site root often redirects into a locale.
    entry_url, entry = http.fetch_text(_seed(base_url))
    soup = BeautifulSoup(entry, "html.parser")
    sidebar, adopters = _sidebar(soup, entry_url)
    sitemap = _sitemap.sitemap_link(entry, entry_url)
    child_failed = False
    if sitemap is None:
        locs, site_root = sidebar, _sitemap.directory(_root_url(entry_url, ()))
    else:
        http.polite_sleep()
        final, locs, child_failed = _sitemap.read_locs(sitemap)
        site_root = _sitemap.directory(final)

    own, roots = _locale_roots(soup, entry_url)
    candidates = [
        u
        for u in _sitemap.unique(locs)
        if _in_locale(u, own, roots) and not _sitemap.is_error_page(u)
    ]
    scope = _sitemap.directory(entry_url)
    if not any(
        _sitemap.under(u, scope) and _sitemap.page_key(u) != _sitemap.page_key(scope)
        for u in candidates
    ):
        scope = _root_url(entry_url, _segs(entry_url)[: len(own)]) if own is not None else site_root

    pages = _reading_order([u for u in candidates if _sitemap.under(u, scope)], sidebar, adopters)
    truncated = child_failed
    if len(pages) > _MAX_PAGES:
        log.warning("starlight.capped", found=len(pages), cap=_MAX_PAGES)
        pages = pages[:_MAX_PAGES]
        truncated = True

    crawl = _sitemap.crawl(
        pages,
        workdir / "raw",
        extract=_extract,
        in_scope=lambda u: _sitemap.under(u, scope) and _in_locale(u, own, roots),
        belongs=is_starlight,
        event="starlight",
    )
    if crawl.saved == 0:
        raise InvalidInputError(
            f"{entry_url} is a Starlight site but no page under {scope} has a "
            f"{_BODY_CSS} body — the theme renamed it, and guessing at another node "
            "would stage a hollow deliverable."
        )

    site_segs = _segs(site_root)
    scope_segs = _segs(scope)
    rel = scope_segs[len(site_segs) :] if scope_segs[: len(site_segs)] == site_segs else scope_segs
    slug = "-".join([slug, *rel])
    log.info(
        "starlight.acquire",
        scope=scope,
        sitemap=sitemap,
        pages=crawl.saved,
        lost=crawl.lost,
        slug=slug,
        truncated=truncated or crawl.stalled,
    )
    return AcquireResult(
        raw_dir=workdir / "raw",
        kind="html",
        slug=slug,
        pages=crawl.saved,
        title=title,
        truncated=truncated or crawl.stalled,
        lost=crawl.lost,
    )
