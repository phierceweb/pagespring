"""Which markdown files a Docsify site is made of: the ``loadSidebar`` file in each page's directory
or above it, as the runtime looks one up. An ``index.html`` answer means absent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import urlparse

from pf_core.log import get_logger

from pagespring import http
from pagespring.patterns._docsify_routes import (
    Site,
    file_url,
    link_options,
    route_dir,
    route_of,
)
from pagespring.patterns._md_code import outside_code

log = get_logger(__name__)

ABSENT = frozenset({403, 404, 410})  # a missing file, S3 answering 403
_APP_SHELL_RE = re.compile(r"^\s*<(?:!doctype|html)\b", re.I)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_PLACEHOLDER_RE = re.compile(r"\0\d+\0")  # a code span outside_code hid
_LINK_RE = re.compile(
    r"(?<!!)\[(?P<label>(?:[^\[\]\n]|\[[^\]\n]*\])*)\]\(\s*<?(?P<target>[^\s)>]+)"
    r"(?:>?\s+(?P<q>['\"])(?P<title>[^'\"\n]*)(?P=q))?"
    r"|<a\b[^>]*?\bhref=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<text>.*?)</a>",
    re.S,
)


@dataclass(frozen=True)
class Page:
    """A markdown file the site loads, the route it is shown at, and its sidebar label."""

    url: str
    label: str | None
    route: str


def is_app_shell(text: str) -> bool:
    """The app's HTML page, served where a markdown file was asked for."""
    return bool(_APP_SHELL_RE.match(text))


def strip_comments(text: str) -> str:
    return _HTML_COMMENT_RE.sub("", text)


def plain(html: str) -> str:
    return _TAG_RE.sub("", html)


def _read(url: str, *, probe: bool) -> list[tuple[str, str]] | None:
    """(label, target) per link of the sidebar file at ``url``, or None: absent on any failure for a
    ``probe``, as the runtime falls back, else only on a refusal (S3 answers 403)."""
    http.polite_sleep()
    try:
        _final, md = http.fetch_text(url)
    except HTTPError as exc:
        if not probe and exc.code not in ABSENT:
            raise
        log.info("docsify.sidebar_unavailable", url=url, status=exc.code)
        return None
    if not md.strip() or is_app_shell(md):
        return None
    return _links(md)


def _links(md: str) -> list[tuple[str, str]]:
    """(label, target) per link in ``md``, in order; an ``:include`` embeds a file
    rather than linking a page."""
    entries: list[tuple[str, str]] = []
    for m in _LINK_RE.finditer(strip_comments(md)):
        if m.group("title") and "include" in link_options(m.group("title")):
            continue
        target = m.group("target") or m.group("href")
        label = m.group("label") if m.group("target") else plain(m.group("text"))
        entries.append((label.strip(), target))
    return entries


def linked_pages(md: str, route: str, site: Site, dirs: set[str]) -> list[Page]:
    """The markdown pages ``md`` links to outside its code, in order, kept to ``dirs``:
    a link out of the directories the sidebar covers is another manual (a translation)."""
    links: list[tuple[str, str]] = []

    def scan(text: str) -> str:
        links.extend(_links(text))
        return text

    outside_code(md, scan)
    base = route_dir(route) if site.relative_links else "/"
    pages: list[Page] = []
    for label, target in links:
        found = route_of(target, base)
        if found is None:
            continue
        url = file_url(found[0], site)
        if urlparse(url).path.endswith(site.ext) and route_dir(found[0]) in dirs:
            pages.append(Page(url, _PLACEHOLDER_RE.sub("", label).strip() or None, found[0]))
    return pages


def _dirs(route: str) -> list[str]:
    """``route``'s directory and each one above it, nearest first, the root left out."""
    head = route if route.endswith("/") else route.rsplit("/", 1)[0] + "/"
    out: list[str] = []
    while head != "/":
        out.append(head)
        head = head.rstrip("/").rsplit("/", 1)[0] + "/"
    return out


def sidebar_pages(site: Site, setting: str | bool | None, *, cap: int) -> list[Page] | None:
    """The pages the sidebar lists in order, a directory's own sidebar after that directory's first
    page; None without a sidebar. The homepage is left to the caller."""
    if setting is False:
        return None
    name = (setting if isinstance(setting, str) and setting else f"_sidebar{site.ext}").lstrip("/")
    root_url = file_url(f"/{name}", site)
    entries = _read(root_url, probe=setting is None)
    if entries is None:
        log.warning("docsify.no_sidebar", url=root_url)
        return None
    pages: list[Page] = []
    known: set[str] = set()

    def add(at: int, entries: list[tuple[str, str]], base: str) -> int:
        for label, target in entries:
            found = route_of(target, base)
            if found is None or (url := file_url(found[0], site)) in known:
                continue
            known.add(url)
            pages.insert(at, Page(url, label, found[0]))
            at += 1
        return at

    add(0, entries, "/")
    known.add(file_url("/", site))
    probed = {root_url}
    i = 0
    while i < len(pages) and len(pages) <= cap:
        at = i + 1
        for directory in _dirs(pages[i].route):
            url = file_url(f"{directory}{name}", site)
            if url not in probed:
                probed.add(url)
                nested = _read(url, probe=True)
                if nested:
                    log.info("docsify.directory_sidebar", url=url)
                    at = add(at, nested, directory if site.relative_links else "/")
        i += 1
    return pages
