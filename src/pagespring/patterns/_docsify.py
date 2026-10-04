"""Docsify for docs_probe: the raw markdown the runtime loads per route, in the order the sidebar
files list it; links resolve to the files they route to, images to the route's directory."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from pf_core.log import get_logger
from pf_core.utils.hashing import content_hash

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns._docsify_embed import embed
from pagespring.patterns._docsify_pages import (
    ABSENT,
    Page,
    is_app_shell,
    linked_pages,
    plain,
    sidebar_pages,
    strip_comments,
)
from pagespring.patterns._docsify_routes import (
    Site,
    asset_url,
    config,
    file_url,
    link_url,
    option,
    read_site,
    route_dir,
)
from pagespring.patterns._md_code import outside_code
from pagespring.patterns._site import raw_stem

log = get_logger(__name__)

_MAX_PAGES = 1000
_RUNTIME_RE = re.compile(r"(?:^|/)docsify(?:@[^/]*)?(?:/|$)|(?:^|/)docsify(?:\.min)?\.js$")
_MD_IMAGE_RE = re.compile(r"(!\[[^\]\n]*\]\(\s*<?)([^\s)>]+)")
_MD_LINK_RE = re.compile(r"((?<!!)\[(?:[^\[\]\n]|\[[^\]\n]*\])*\]\(\s*<?)([^\s)>]+)")
_HTML_IMAGE_RE = re.compile(r"(<img\b[^>]*?\bsrc=[\"'])([^\"']+)", re.I)
_HTML_LINK_RE = re.compile(r"(<a\b[^>]*?\bhref=[\"'])([^\"']+)", re.I)
# Docsify's "important" (!>) and "tip" (?>) paragraphs.
_CALLOUT_RE = re.compile(r"^([ \t]*)[!?]>[ \t]?", re.M)
_TITLE_RE = re.compile(r"^ {0,3}#[ \t]|<h1\b|^[^\n]+\n=+[ \t]*$", re.I | re.M)


def is_docsify(html: str) -> bool:
    """A script loading the Docsify runtime (a plugin alone is not the runtime)."""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", src=True):
        src = script.get("src")
        if isinstance(src, str) and _RUNTIME_RE.search(urlparse(src).path):
            return True
    return False


def _rewrite(md: str, route: str, site: Site) -> str:
    def rewrite(text: str) -> str:
        text = _MD_IMAGE_RE.sub(lambda m: m.group(1) + asset_url(m.group(2), route, site), text)
        text = _HTML_IMAGE_RE.sub(lambda m: m.group(1) + asset_url(m.group(2), route, site), text)
        text = _MD_LINK_RE.sub(lambda m: m.group(1) + link_url(m.group(2), route, site), text)
        text = _HTML_LINK_RE.sub(lambda m: m.group(1) + link_url(m.group(2), route, site), text)
        return _CALLOUT_RE.sub(r"\1> ", text)

    return outside_code(md, rewrite)


def _has_title(md: str) -> bool:
    found = False

    def scan(text: str) -> str:
        nonlocal found
        found = found or bool(_TITLE_RE.search(strip_comments(text)))
        return text

    outside_code(md, scan)
    return found


def _titled(md: str, label: str | None) -> str:
    """``md`` with an H1, the sidebar label on top when the page has none."""
    md = md.lstrip("\ufeff").strip()
    if not label or _has_title(md):
        return md
    return f"# {label}\n\n{md}"


def acquire(base_url: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    page_url, index = http.fetch_text(base_url.split("#", 1)[0])
    js = config(index)
    site = read_site(page_url, js)
    name = option(js, "name")
    doc_title = plain(name).strip() if isinstance(name, str) else ""

    listed = sidebar_pages(site, option(js, "loadSidebar"), cap=_MAX_PAGES)
    pages = list(listed or [])
    home = file_url("/", site)
    if all(page.url != home for page in pages):
        pages.insert(0, Page(home, doc_title or None, "/"))
    truncated = len(pages) > _MAX_PAGES
    if truncated:
        log.warning("docsify.capped", found=len(pages), cap=_MAX_PAGES)
        pages = pages[:_MAX_PAGES]

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    dirs = {route_dir(p.route) for p in pages}
    known = {p.url for p in pages}
    # Found in page content: a dead link there is the site's, not a lost page.
    unlisted: set[str] = set()
    staged: set[str] = set()
    loaded: dict[str, str | None] = {}
    saved = 0
    lost = 0
    i = 0
    while i < len(pages):
        pos, (url, label, route) = i, (pages[i].url, pages[i].label, pages[i].route)
        i += 1
        http.polite_sleep()
        try:
            _final, md = http.fetch_text(url)
        except Exception as exc:
            dead = url in unlisted and isinstance(exc, HTTPError) and exc.code in ABSENT
            lost += 0 if dead else 1
            log.warning("docsify.fetch_error", url=url, error=str(exc))
            continue
        # A missing file answered with the app's own index.html is a soft 404.
        if not md.strip() or is_app_shell(md):
            lost += 0 if url in unlisted else 1
            log.warning("docsify.no_markdown", url=url)
            continue
        digest = content_hash(md)
        if digest in staged:
            log.info("docsify.duplicate_page", url=url)
            continue
        staged.add(digest)
        stem = raw_stem(urlparse(url).path.removesuffix(site.ext))
        body = _titled(_rewrite(embed(md, route, site, loaded), route, site), label)
        (raw_dir / f"{pos:04d}-{stem}.md").write_text(
            f"<!-- source: {url} -->\n\n{body}\n", encoding="utf-8"
        )
        saved += 1
        found = [p for p in linked_pages(md, route, site, dirs) if p.url not in known]
        if len(pages) + len(found) > _MAX_PAGES:
            truncated = True
            found = found[: max(_MAX_PAGES - len(pages), 0)]
        known.update(p.url for p in found)
        unlisted.update(p.url for p in found)
        pages[i:i] = found

    log.info("docsify.acquire", root=site.root, pages=saved, lost=lost, slug=slug)
    return AcquireResult(
        raw_dir=raw_dir,
        kind="markdown",
        slug=slug,
        pages=saved,
        title=doc_title or title,
        truncated=truncated,
        single_document=listed is None,
        lost=lost,
    )
