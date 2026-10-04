"""Antora for docs_probe: one component version in nav order, plus pages only the component's own
sitemap lists under the version's directory that render an authored ``h1.page`` title."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from bs4.element import Comment, Tag
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger
from pf_core.utils.hashing import content_hash
from pf_core.utils.slugify import slugify

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns._site import (
    absolutize_refs,
    flatten_responsive_images,
    generator_meta,
    names_a_file,
    raw_stem,
    strip_scripts,
)

log = get_logger(__name__)

_MAX_PAGES = 2000
_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_CHROME_CSS = (
    "nav.pagination, aside.toc, nav.breadcrumbs, div.breadcrumbs-container, "
    "div.edit-this-page, p.contribute, #search-field"
)


def is_antora(html: str) -> bool:
    """Generator meta naming Antora, or its layout: ``article.doc`` beside ``nav.nav-menu``."""
    if any(tag.strip().startswith("antora") for tag in generator_meta(html).split(",")):
        return True
    soup = BeautifulSoup(html, "html.parser")
    return (
        soup.select_one("article.doc") is not None and soup.select_one("nav.nav-menu") is not None
    )


def _clean(url: str) -> str:
    return url.split("#", 1)[0].split("?", 1)[0]


def _dir(url: str) -> str:
    """``url``'s directory, ending in a slash."""
    p = urlparse(_clean(url))
    last = p.path.rsplit("/", 1)[-1]
    path = p.path[: -len(last)] if names_a_file(last) else p.path.rstrip("/") + "/"
    return f"{p.scheme}://{p.netloc}{path}"


def _parent(directory: str) -> str:
    p = urlparse(directory)
    return f"{p.scheme}://{p.netloc}{p.path.rstrip('/').rsplit('/', 1)[0]}/"


def _common_dir(dirs: list[str]) -> str:
    p = urlparse(dirs[0])
    common: list[str] = []
    for segs in zip(*([s for s in urlparse(d).path.split("/") if s] for d in dirs), strict=False):
        if len(set(segs)) != 1:
            break
        common.append(segs[0])
    return f"{p.scheme}://{p.netloc}/" + "".join(f"{s}/" for s in common)


def _page_url(href: object, base: str) -> str | None:
    """``href`` as an absolute page URL on ``base``'s host; None for anything else."""
    if not isinstance(href, str):
        return None
    url = _clean(urljoin(base, href))
    p = urlparse(url)
    if p.scheme not in ("http", "https") or p.netloc != urlparse(base).netloc:
        return None
    last = p.path.rsplit("/", 1)[-1]
    if names_a_file(last) and not last.lower().endswith((".html", ".htm")):
        return None
    return url


def _nav(soup: BeautifulSoup, page_url: str) -> tuple[str | None, list[str]]:
    """The nav's start-page link and its page links in order, each once."""
    menu = soup.select_one("nav.nav-menu")
    if menu is None:
        return None, []
    title = menu.select_one(".title a[href]")
    start = _page_url(title.get("href"), page_url) if title else None
    links = (_page_url(a.get("href"), page_url) for a in menu.select("a.nav-link[href]"))
    return start, list(dict.fromkeys(u for u in links if u))


def _scope_root(anchors: list[str], links: list[str]) -> str:
    """Deepest directory holding every anchor and most nav pages: one stray link to another
    component must not widen the scope, nor a deep start page narrow it."""
    root = _common_dir([_dir(a) for a in anchors])
    while _parent(root) != root and (
        not all(_clean(a).startswith(root) for a in anchors)
        or (links and sum(u.startswith(root) for u in links) * 2 <= len(links))
    ):
        root = _parent(root)
    return root


def _fetch_sitemap(url: str) -> str | None:
    try:
        _final, body = http.fetch_text(url)
    except Exception as exc:
        log.debug("antora.no_sitemap", url=url, error=str(exc))
        body = ""
    http.polite_sleep()
    return body if "<urlset" in body or "<sitemapindex" in body else None


def _locs(body: str) -> tuple[list[str], bool]:
    """A sitemap's ``<loc>`` URLs, and whether they name child sitemaps."""
    root = ET.fromstring(body)
    locs = [el.text.strip() for el in root.iter(f"{_NS}loc") if el.text]
    return locs, root.tag == f"{_NS}sitemapindex"


def _sitemap_pages(root: str, component: str | None) -> tuple[list[str] | None, str | None]:
    """URLs under ``root`` from the component's sitemap (None when none was read),
    and the site root directory the sitemap was found in."""
    site = root
    while (body := _fetch_sitemap(f"{site}sitemap.xml")) is None:
        if _parent(site) == site:
            return None, None
        site = _parent(site)
    try:
        locs, is_index = _locs(body)
        if is_index:
            segs = [s for s in root[len(site) :].split("/") if s]
            names = [n for n in (component, *reversed(segs), "ROOT") if n]
            children = {loc.rsplit("/", 1)[-1]: loc for loc in locs}
            child = next(
                (children[f"sitemap-{n}.xml"] for n in names if f"sitemap-{n}.xml" in children),
                None,
            )
            if child is None:
                log.info("antora.no_component_sitemap", sitemap=f"{site}sitemap.xml", tried=names)
                return None, site
            child_body = _fetch_sitemap(child)
            if child_body is None:
                log.warning("antora.sitemap_unreadable", url=child)
                return None, site
            locs, _nested = _locs(child_body)
    except ET.ParseError as exc:
        log.warning("antora.sitemap_unparsable", site=site, error=str(exc))
        return None, site
    return [u for u in locs if u.startswith(root)], site


def _seed(url: str) -> str:
    """A directory seed with its trailing slash restored: it is the urljoin base."""
    p = urlparse(url.split("#", 1)[0])
    if p.path.endswith("/") or names_a_file(p.path.rsplit("/", 1)[-1]):
        return urlunparse(p)
    return urlunparse(p._replace(path=p.path + "/"))


def _article(html: str, page_url: str) -> Tag | None:
    article = BeautifulSoup(html, "html.parser").select_one("article.doc")
    if article is None:
        return None
    for el in article.select(_CHROME_CSS):
        el.decompose()
    for comment in article.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    strip_scripts(article)
    flatten_responsive_images(article)
    absolutize_refs(article, page_url)
    return article


def _title(soup: BeautifulSoup, fallback: str | None) -> str | None:
    """The component version's name as the UI shows it (``Antora 3.2``)."""
    for css in (".nav-panel-explore .context", "nav.nav-menu .title"):
        node = soup.select_one(css)
        text = node.get_text(" ", strip=True) if node else ""
        if text:
            return text
    return fallback


def acquire(url: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    seed = _seed(url)
    try:
        entry_url, entry = http.fetch_text(seed)
    except HTTPError:
        if seed == url:
            raise
        entry_url, entry = http.fetch_text(url)  # an extensionless page, not a directory
    http.polite_sleep()
    soup = BeautifulSoup(entry, "html.parser")
    start, nav = _nav(soup, entry_url)
    root = _scope_root([entry_url, *([start] if start else [])], nav)
    container = soup.select_one(".nav-container[data-component]")
    component = str(container["data-component"]) if container else None
    listed, site = _sitemap_pages(root, component)

    ordered = list(dict.fromkeys(u for u in (start, *nav, entry_url) if u and u.startswith(root)))
    orphans = [u for u in dict.fromkeys(listed or []) if u not in ordered]
    pending = [(u, False) for u in ordered] + [(u, True) for u in orphans]

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    bodies = {entry_url: entry}
    staged: set[str] = set()
    hashes: set[str] = set()
    saved = lost = skipped = 0
    truncated = False
    for page, orphan in pending:
        if saved >= _MAX_PAGES:
            truncated = True
            break
        final, body = page, bodies.pop(page, None)
        if body is None:
            try:
                final, body = http.fetch_text(page)
            except Exception as exc:
                lost += 1
                log.warning("antora.fetch_error", url=page, error=str(exc))
                http.polite_sleep()
                continue
            http.polite_sleep()
        if not final.startswith(root) or final in staged:
            log.info("antora.redirected", url=page, final=final, root=root)
            continue
        article = _article(body, final)
        if article is None:
            lost += 1
            log.warning("antora.no_article", url=final)
            continue
        if orphan and article.select_one("h1.page") is None:
            skipped += 1
            continue
        fragment = str(article)
        digest = content_hash(fragment)
        if digest in hashes:
            continue
        staged.add(final)
        hashes.add(digest)
        stem = raw_stem(final[len(root) :])
        (raw_dir / f"{saved:04d}-{stem}.html").write_text(
            f"<!-- source: {final} -->\n<section>\n{fragment}\n</section>\n", encoding="utf-8"
        )
        saved += 1

    if saved == 0:
        raise InvalidInputError(
            f"{entry_url} is an Antora site, but none of the {len(pending)} pages under {root} "
            f"yielded an article.doc container ({lost} lost) — the UI may have renamed it."
        )
    if truncated:
        log.warning("antora.capped", saved=saved, cap=_MAX_PAGES, pending=len(pending))

    # One host serves many components, so the version's path names the manual.
    rel = root[len(site) :] if site and root.startswith(site) else urlparse(root).path
    slug = slugify("-".join(dict.fromkeys([slug, *(s for s in rel.split("/") if s)]))) or slug
    proven = listed is not None or soup.select_one("nav.nav-menu") is not None
    log.info(
        "antora.acquire",
        root=root,
        sitemap=site,
        pages=saved,
        lost=lost,
        skipped=skipped,
        slug=slug,
        truncated=truncated,
    )
    return AcquireResult(
        raw_dir=raw_dir,
        kind="html",
        slug=slug,
        pages=saved,
        title=_title(soup, title),
        truncated=truncated,
        single_document=saved == 1 and len(pending) == 1 and proven,
        lost=lost,
    )
