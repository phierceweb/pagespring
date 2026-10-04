"""VitePress for docs_probe: the entry's sidebar, each sidebar a listed page adds, then each top-nav
section's (a home page follows its hero action); content is ``.vp-doc``."""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger
from pf_core.utils.hashing import content_hash

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.config import cfg
from pagespring.liveness import ProgressWatchdog
from pagespring.patterns._shiki import flatten_shiki
from pagespring.patterns._site import (
    absolutize_refs,
    flatten_responsive_images,
    generator_meta,
    names_a_file,
    raw_stem,
    strip_scripts,
)

log = get_logger(__name__)

_MAX_PAGES = 1000
# The default theme's appearance key, set by an inline script in every page head.
_THEME_KEY = "vitepress-theme-appearance"
_SIDEBAR_CSS = "aside.VPSidebar"
# The main menu only: translations and social links sit in sibling menus.
_NAV_MENU_CSS = ".VPNavBarMenu a"
_HERO_LINKS_CSS = ".VPHero .actions a"
_CONTENT_CSS = ".vp-doc"
# Buttons are controls (copy, promo calls to action), never documentation text.
_CHROME_CSS = (
    "a.header-anchor, button, span.lang, .line-numbers-wrapper, "
    ".v-popper__popper, [hidden], a[rel~=sponsored]"
)


def is_vitepress(html: str) -> bool:
    """Generator meta, or the default theme's appearance script."""
    if "vitepress" in generator_meta(html):
        return True
    soup = BeautifulSoup(html, "html.parser")
    return any(_THEME_KEY in script.get_text() for script in soup.find_all("script"))


def _page_key(url: str) -> str:
    """One key for a page's URL forms: ``/a/``, ``/a/index.html``, ``/a.html`` and ``/a``."""
    p = urlparse(urldefrag(url).url)
    path = p.path.removesuffix(".html").removesuffix("/index").rstrip("/")
    return f"{p.netloc}{path}"


def _same_host_pages(links: list[Tag], page_url: str, host: str) -> list[str]:
    out: list[str] = []
    for a in links:
        href = a.get("href")
        if not isinstance(href, str) or not href:
            continue
        url = urldefrag(urljoin(page_url, href)).url
        p = urlparse(url)
        if p.scheme not in ("http", "https") or p.netloc != host:
            continue
        last = p.path.rsplit("/", 1)[-1]
        if names_a_file(last) and not last.endswith(".html"):
            continue
        out.append(url)
    return out


def _sidebar(soup: BeautifulSoup, page_url: str, host: str) -> list[str] | None:
    aside = soup.select_one(_SIDEBAR_CSS)
    if aside is None:
        return None
    return _same_host_pages(aside.find_all("a"), page_url, host)


def _label(block: Tag, text: str) -> None:
    """Put ``text`` in front of ``block`` as a bold paragraph."""
    para = BeautifulSoup("<p><strong></strong></p>", "html.parser").p
    if para is not None and para.strong is not None:
        para.strong.string = text
        block.insert_before(para)


def _label_tabs(root: Tag) -> None:
    """Name each tabbed block by its tab, then drop the tab controls.

    A tab panel the page did not render is absent from the HTML; its tab goes too."""
    for group in root.select(".vp-code-group"):
        labels = [label.get_text(" ", strip=True) for label in group.select(".tabs label")]
        blocks = group.select(".blocks > div")
        if len(labels) == len(blocks):
            for label, block in zip(labels, blocks, strict=True):
                _label(block, label)
        for tabs in group.select(".tabs"):
            tabs.decompose()
    for group in root.select(".plugin-tabs"):
        names = {
            str(button.get("id")): button.get_text(" ", strip=True)
            for button in group.select(".plugin-tabs--tab")
        }
        for panel in group.select(".plugin-tabs--content"):
            name = names.get(str(panel.get("aria-labelledby")))
            first = panel.find(True)
            if name and isinstance(first, Tag):
                _label(first, name)
        for tab_list in group.select(".plugin-tabs--tab-list"):
            tab_list.decompose()


def _extract(soup: BeautifulSoup, page_url: str) -> str | None:
    doc = soup.select_one(_CONTENT_CSS)
    if doc is None:
        return None
    _label_tabs(doc)
    for el in doc.select(_CHROME_CSS):
        el.decompose()
    strip_scripts(doc)
    flatten_shiki(doc)
    flatten_responsive_images(doc)
    absolutize_refs(doc, page_url)
    return str(doc)


def _entry(url: str) -> tuple[str, BeautifulSoup, list[str]]:
    """The first page with a sidebar: the entry itself, or its hero's first action."""
    final, body = http.fetch_text(url)
    host = urlparse(final).netloc
    soup = BeautifulSoup(body, "html.parser")
    links = _sidebar(soup, final, host)
    if links is None:
        hero = _same_host_pages(soup.select(_HERO_LINKS_CSS), final, host)
        if hero:
            log.info("vitepress.follow_hero", entry=final, to=hero[0])
            http.polite_sleep()
            final, body = http.fetch_text(hero[0])
            soup = BeautifulSoup(body, "html.parser")
            links = _sidebar(soup, final, host)
    if links is None:
        raise InvalidInputError(
            f"{url} is a VitePress page with no sidebar to crawl — point at a page "
            "inside the docs (one that shows the sidebar)."
        )
    return final, soup, links


def _site_title(soup: BeautifulSoup) -> str | None:
    """The site part of a ``Page | Site`` title, VitePress's default template."""
    title = soup.title.get_text(strip=True) if soup.title else ""
    return title.rsplit(" | ", 1)[-1].strip() or None


def _section(url: str, host: str) -> tuple[str, BeautifulSoup, list[str]] | None:
    """A nav page with the sidebar it renders; None when it renders none."""
    http.polite_sleep()
    try:
        final, body = http.fetch_text(url)
    except Exception as exc:
        log.warning("vitepress.nav_fetch_error", url=url, error=str(exc))
        return None
    soup = BeautifulSoup(body, "html.parser")
    links = _sidebar(soup, final, host) if urlparse(final).netloc == host else None
    if links is None:
        log.info("vitepress.nav_without_sidebar", url=final)
        return None
    return final, soup, links


def acquire(base_url: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    entry_url, entry_soup, links = _entry(base_url)
    host = urlparse(entry_url).netloc
    doc_title = _site_title(entry_soup) or title
    nav = _same_host_pages(entry_soup.select(_NAV_MENU_CSS), entry_url, host)

    # The sidebar's form of the entry's URL is canonical: a slashless seed would
    # resolve the page's relative refs one directory too high.
    entry_key = _page_key(entry_url)
    entry_url = next((u for u in links if _page_key(u) == entry_key), entry_url)
    order: list[str] = []
    keys: set[str] = set()

    def enqueue(urls: list[str]) -> None:
        for url in urls:
            if _page_key(url) not in keys:
                keys.add(_page_key(url))
                order.append(url)

    enqueue(links if entry_url in links else [entry_url, *links])

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    cached: dict[str, BeautifulSoup] = {entry_url: entry_soup}
    staged: set[str] = set()
    saved = 0
    lost = 0
    watchdog = ProgressWatchdog(stall_after_s=cfg.CRAWL_STALL_AFTER_S, now=time.monotonic)
    i = 0
    while saved < _MAX_PAGES:
        if watchdog.stalled():
            idle = round(watchdog.idle_s())
            log.warning("vitepress.stalled", saved=saved, idle_s=idle, queued=len(order) - i)
            break
        if i == len(order):
            if not nav:
                break
            seed = nav.pop(0)
            section = None if _page_key(seed) in keys else _section(seed, host)
            if section is not None:
                page, page_soup, page_links = section
                listed = next((u for u in page_links if _page_key(u) == _page_key(page)), None)
                enqueue(page_links if listed else [page, *page_links])
                cached[listed or page] = page_soup
            continue
        url = order[i]
        position = i
        i += 1
        soup = cached.pop(url, None)
        final = url
        if soup is None:
            http.polite_sleep()
            try:
                final, body = http.fetch_text(url)
            except Exception as exc:
                lost += 1
                log.warning("vitepress.fetch_error", url=url, error=str(exc))
                continue
            if urlparse(final).netloc != host:
                log.warning("vitepress.left_host", url=url, final=final)
                continue
            if not is_vitepress(body):
                log.info("vitepress.not_vitepress", url=final)
                continue
            soup = BeautifulSoup(body, "html.parser")
        enqueue(_sidebar(soup, final, host) or [])
        fragment = _extract(soup, final)
        if fragment is None:
            lost += 1
            log.warning("vitepress.no_content", url=final)
            continue
        # A directory URL and its index.html, or a redirect alias, are one page.
        digest = content_hash(fragment)
        if digest in staged:
            log.info("vitepress.duplicate_page", url=final)
            continue
        staged.add(digest)
        stem = raw_stem(urlparse(url).path.removesuffix(".html"))
        (raw_dir / f"{position:04d}-{stem}.html").write_text(
            f"<!-- source: {final} -->\n<section>\n{fragment}\n</section>\n", encoding="utf-8"
        )
        saved += 1
        watchdog.progress()

    if saved == 0:
        raise InvalidInputError(
            f"{entry_url} is VitePress but no page its sidebar lists has a {_CONTENT_CSS} "
            "container — the site's theme replaced the default one."
        )
    truncated = i < len(order) or any(_page_key(u) not in keys for u in nav)
    if truncated:
        log.warning("vitepress.capped", saved=saved, cap=_MAX_PAGES, queued=len(order) - i)
    log.info("vitepress.acquire", entry=entry_url, pages=saved, lost=lost, slug=slug)
    return AcquireResult(
        raw_dir=raw_dir,
        kind="html",
        slug=slug,
        pages=saved,
        title=doc_title,
        truncated=truncated,
        lost=lost,
    )
