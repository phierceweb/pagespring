"""WordPress acquisition for docs_probe — one post via the REST API, no crawl.

WordPress declares each post's REST endpoint in the page head:

    <link rel="alternate" type="application/json" href=".../wp-json/wp/v2/posts/<id>" />

Reading that beats deriving a path — the install may sit in a subdirectory
(``/beat/``), and ``pagespring.http`` exposes no response headers, so the
``Link:`` header route is unavailable.

Scope is ONE post. A whole-blog crawl is a blog scrape, not a manual.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup
from bs4.element import Tag
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger
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

_REST_LINK_RE = re.compile(
    r'<link[^>]+rel="alternate"[^>]+type="application/json"[^>]+href="([^"]+)"', re.I
)
# Plugin-injected on-page navigation, not manual content.
_CHROME_CSS = "#rank-math-toc, .rank-math-toc"


def is_wordpress(html: str) -> bool:
    """Generator meta, or the wp-json link the head declares.

    Hardened installs strip the generator tag, so the REST link is the reliable
    tell — and it is the same link ``acquire`` reads."""
    if "wordpress" in generator_meta(html):
        return True
    endpoint = rest_endpoint(html)
    return bool(endpoint and "/wp-json/" in endpoint)


def rest_endpoint(html: str) -> str | None:
    """The post's wp-json REST URL as declared in the head, or None."""
    m = _REST_LINK_RE.search(html)
    return m.group(1) if m else None


def _clean(rendered: str, page_url: str) -> str:
    soup = BeautifulSoup(rendered, "html.parser")
    for junk in soup.select(_CHROME_CSS):
        junk.decompose()
    strip_scripts(soup)
    # Gutenberg leaves runs of empty <p>. Test for no child elements at all: a
    # <p> wrapping only an iframe or video has no text either.
    for para in soup.find_all("p"):
        if isinstance(para, Tag) and not para.get_text(strip=True) and not para.find(True):
            para.decompose()
    flatten_responsive_images(soup)
    absolutize_refs(soup, page_url)
    return str(soup)


def acquire(url: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    _f, page = http.fetch_text(url)
    endpoint = rest_endpoint(page)
    if endpoint is None:
        raise InvalidInputError(
            f"{url} is WordPress but declares no wp-json REST link in its head — "
            "probed <link rel=alternate type=application/json>. Without it the "
            "post body cannot be read without guessing at theme markup."
        )
    try:
        _f2, body = http.fetch_text(endpoint)
        post = json.loads(body)
    except Exception as exc:
        raise InvalidInputError(f"{endpoint} is not a readable WP REST post: {exc}") from exc

    rendered = (post.get("content") or {}).get("rendered") or ""
    # After cleaning, not before: a nav-only body passes a raw check and then
    # cleans to nothing.
    cleaned = _clean(rendered, url)
    if not BeautifulSoup(cleaned, "html.parser").get_text(strip=True):
        raise InvalidInputError(
            f"{endpoint} has no content once navigation and empty blocks are "
            "removed — the post is a stub or the REST endpoint is wrong."
        )

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "0000.html").write_text(
        f"<!-- source: {url} -->\n<section>\n{cleaned}\n</section>\n",
        encoding="utf-8",
    )

    post_slug = slugify(str(post.get("slug") or "")) or slug
    # title.rendered is HTML-escaped by wptexturize ("Tuning &#038; Care").
    raw_title = (post.get("title") or {}).get("rendered")
    post_title = html.unescape(raw_title) if raw_title else title
    log.info("wordpress.acquire", url=url, endpoint=endpoint, slug=post_slug)
    return AcquireResult(
        raw_dir=raw_dir,
        kind="html",
        slug=post_slug,
        pages=1,
        title=post_title,
        single_document=True,
    )
