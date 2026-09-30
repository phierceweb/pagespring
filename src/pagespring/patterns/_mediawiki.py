"""MediaWiki acquisition for docs_probe — the action API, no HTML crawl.

A wiki is a link graph, not a book. One manual is the seed page plus the pages
its content links to, in reading order: a wiki manual's landing page is its
table of contents, and following links any further walks the whole wiki.

``action=parse`` returns a page's rendered content without the skin. Its link
list carries namespace and existence but is alphabetical, so the order comes
from the rendered anchors.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

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
    strip_scripts,
)

log = get_logger(__name__)

_MAX_PAGES = 500
# Current config (``"wgPageName":"X"``) and the pre-ResourceLoader global (``wgPageName = "X"``).
_PAGE_NAME_RE = re.compile(r'\bwgPageName"?\s*[:=]\s*("(?:[^"\\]|\\.)*")')
_NAMESPACE_RE = re.compile(r'\bwgNamespaceNumber"?\s*[:=]\s*(-?\d+)')
_CHROME_CSS = ".mw-editsection, .editsection, #toc, .toc"
# A title runs to 255 bytes; the staged filename must stay under the same limit.
_STEM_CHARS = 80


def _edit_uri(page: str) -> str | None:
    """The head's EditURI href when it names ``api.php``; WordPress's names xmlrpc.php."""
    link = BeautifulSoup(page, "html.parser").find("link", rel="EditURI")
    href = link.get("href") if isinstance(link, Tag) else None
    return href if isinstance(href, str) and "api.php" in href else None


def page_name(page: str) -> str | None:
    """The page's ``wgPageName`` from its inline config, or None."""
    m = _PAGE_NAME_RE.search(page)
    if m is None:
        return None
    try:
        name = json.loads(m.group(1))
    except ValueError:
        return None
    return name if isinstance(name, str) and name else None


def is_mediawiki(page: str) -> bool:
    """Generator meta, or the ``api.php`` link and page config every install emits.

    A hardened install strips the generator tag; the other two are what
    ``acquire`` reads."""
    if "mediawiki" in generator_meta(page):
        return True
    return _edit_uri(page) is not None and page_name(page) is not None


def api_url(page_url: str, page: str) -> str | None:
    """The wiki's ``api.php`` as the head declares it, on the page's own scheme.

    The declared scheme can lag the site's: ``http://`` on an https wiki."""
    href = _edit_uri(page)
    if href is None:
        return None
    here = urlparse(page_url)
    api = urlparse(urljoin(page_url, href))._replace(query="", fragment="")
    if api.netloc == here.netloc:
        api = api._replace(scheme=here.scheme)
    return api.geturl()


def _parse(api: str, title: str) -> dict[str, Any]:
    """``action=parse`` for one page, redirects followed."""
    query = urlencode(
        {"action": "parse", "format": "json", "redirects": "1", "prop": "text|links", "page": title}
    )
    _f, body = http.fetch_text(f"{api}?{query}")
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise InvalidInputError(f"{api} answered {title!r} with something other than JSON") from exc
    parsed = data.get("parse") if isinstance(data, dict) else None
    if not isinstance(parsed, dict):
        error = data.get("error") if isinstance(data, dict) else None
        code = error.get("code") if isinstance(error, dict) else "no parse result"
        raise InvalidInputError(f"{api} would not parse {title!r}: {code}")
    return parsed


def _text(parsed: dict[str, Any]) -> str:
    text = parsed.get("text")
    if isinstance(text, dict):
        text = text.get("*")
    return text if isinstance(text, str) else ""


def _linked_titles(parsed: dict[str, Any], namespaces: set[int], seed: str) -> list[str]:
    """Existing pages in ``namespaces`` the content links to, in reading order.

    A listed link with no anchor to place it (a template can render one out of
    sight) follows the anchored ones."""
    wanted = [
        str(link["*"])
        for link in parsed.get("links") or []
        if isinstance(link, dict)
        and "exists" in link
        and link.get("ns") in namespaces
        and link.get("*") not in (None, seed)
    ]
    anchored = [
        str(a["title"])
        for a in BeautifulSoup(_text(parsed), "html.parser").find_all("a", title=True)
        if isinstance(a, Tag)
    ]
    first: dict[str, int] = {}
    for i, title in enumerate(anchored):
        first.setdefault(title, i)
    return sorted(wanted, key=lambda t: first.get(t, len(anchored)))


def _clean(content: str, page_url: str) -> str | None:
    """The rendered content without parser chrome, refs absolute; None when empty."""
    soup = BeautifulSoup(content, "html.parser")
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for el in soup.select(_CHROME_CSS):
        el.decompose()
    strip_scripts(soup)
    flatten_responsive_images(soup)
    absolutize_refs(soup, page_url)
    if not soup.get_text(strip=True) and soup.find("img") is None:
        return None
    return str(soup)


def _index_url(api: str, title: str) -> str:
    return urljoin(api, "index.php") + "?" + urlencode({"title": title.replace(" ", "_")})


def _slug(site: str, title: str) -> str:
    """The page title, led by the site name unless the title already starts with it."""
    name = slugify(title)
    if name == site or name.startswith(f"{site}-"):
        return name
    return slugify(f"{site} {title}") or site


def acquire(url: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    seed_url, page = http.fetch_text(url)
    api = api_url(seed_url, page)
    name = page_name(page)
    if api is None or name is None:
        raise InvalidInputError(
            f"{seed_url} is MediaWiki but its head declares no api.php (the EditURI link) "
            "or no wgPageName — the action API is the route to the rendered page."
        )
    ns = _NAMESPACE_RE.search(page)
    namespaces = {0, int(ns.group(1)) if ns else 0}
    http.polite_sleep()
    seed = _parse(api, name)
    seed_title = str(seed.get("title") or name.replace("_", " "))
    linked = _linked_titles(seed, namespaces, seed_title)
    truncated = len(linked) >= _MAX_PAGES
    if truncated:
        log.warning("mediawiki.capped", found=len(linked) + 1, cap=_MAX_PAGES)
        linked = linked[: _MAX_PAGES - 1]

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    staged_titles: set[str] = set()
    staged_hashes: set[str] = set()
    saved = 0
    lost = 0
    for i, want in enumerate([seed_title, *linked]):
        parsed = seed
        if i:
            http.polite_sleep()
            try:
                parsed = _parse(api, want)
            except Exception as exc:
                lost += 1
                log.warning("mediawiki.page_error", title=want, error=str(exc))
                continue
        resolved = str(parsed.get("title") or want)
        if resolved in staged_titles:
            continue
        page_url = _index_url(api, resolved) if i else seed_url
        fragment = _clean(_text(parsed), page_url)
        if fragment is None:
            lost += 1
            log.warning("mediawiki.no_content", title=resolved)
            continue
        staged_titles.add(resolved)
        digest = content_hash(fragment)
        if digest in staged_hashes:
            continue
        staged_hashes.add(digest)
        stem = slugify(resolved)[:_STEM_CHARS] or "page"
        (raw_dir / f"{saved:04d}-{stem}.html").write_text(
            f"<!-- source: {page_url} -->\n<section>\n<h1>{html.escape(resolved)}</h1>\n"
            f"{fragment}\n</section>\n",
            encoding="utf-8",
        )
        saved += 1

    manual_slug = _slug(slug, seed_title)
    log.info(
        "mediawiki.acquire", api=api, seed=seed_title, pages=saved, lost=lost, slug=manual_slug
    )
    return AcquireResult(
        raw_dir=raw_dir,
        kind="html",
        slug=manual_slug,
        pages=saved,
        title=seed_title,
        truncated=truncated,
        single_document=saved == 1 and not linked,
        lost=lost,
    )
