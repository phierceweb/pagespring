"""zendesk_help — Zendesk Help Center sites (e.g. support.<vendor>.com/hc/...).

Uses the Help Center REST API (no scraping): ``/api/v2/help_center/<locale>/
articles.json`` is paginated and returns each article's title + HTML body.
acquire fetches all articles; normalize merges them into one HTML doc. Image
URLs in the bodies are absolute (Zendesk CDN) — pagespeak, or the optional
``--download-images``, handles them.

Point it at the help center, e.g. ``https://support.gingerlabs.com/hc/en-us``,
or at one ``/sections/<id>-...`` / ``/categories/<id>-...`` to pull just that
slice — the right form when one center covers several products.
"""

from __future__ import annotations

import html as _html
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from pf_core.log import get_logger
from pf_core.utils.slugify import slugify

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns._site import (
    absolutize_refs,
    flatten_responsive_images,
    strip_scripts,
)

log = get_logger(__name__)

_MAX_PAGES = 100  # API pages (per_page=100) — safety cap


_SCOPE_RE = re.compile(r"/(sections|categories)/(\d+)")
_ARTICLE_RE = re.compile(r"/articles/(\d+)(?:-([^/?#]+))?")
# Binary uploads served from the same /hc/ path space — a file, not an article.
_ATTACHMENT_SEG = "/article_attachments/"


def _api_base_and_locale(url: str) -> tuple[str, str]:
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    m = re.search(r"/hc/([a-z]{2}-[a-z]{2})", p.path)
    return origin, (m.group(1) if m else "en-us")


def _articles_endpoint(url: str) -> str:
    """Articles endpoint, narrowed when the URL names an article/section/category.

    A bare ``/hc/<locale>`` pulls the whole help center — right for a
    single-product vendor, wrong for one that ships many under one center.
    """
    origin, locale = _api_base_and_locale(url)
    base = f"{origin}/api/v2/help_center/{locale}"
    path = urlparse(url).path
    article = _ARTICLE_RE.search(path)
    if article:
        return f"{base}/articles/{article.group(1)}.json"
    scope = _SCOPE_RE.search(path)
    if scope:
        return f"{base}/{scope.group(1)}/{scope.group(2)}/articles.json?per_page=100"
    return f"{base}/articles.json?per_page=100"


def _slug(url: str) -> str:
    """Host id, plus the article's own name when the URL names one.

    Both halves are load-bearing: a host-only slug collides across articles, an
    article-only slug collides across vendors."""
    article = _ARTICLE_RE.search(urlparse(url).path)
    host = urlparse(url).netloc.lower().removeprefix("www.").removeprefix("support.")
    host_slug = slugify(host.split(".")[0]) or "help"
    if article:
        return slugify(f"{host_slug}-{article.group(2) or article.group(1)}")
    return host_slug


class ZendeskHelpPattern:
    name = "zendesk_help"

    def match(self, url: str) -> bool:
        p = urlparse(url)
        if _ATTACHMENT_SEG in p.path:
            return False  # a file — docs_probe sniffs it and routes by content
        return p.netloc.lower().endswith(".zendesk.com") or "/hc/" in p.path

    def _clean_body(self, body: str, page_url: str) -> str:
        """Article bodies are author-supplied HTML — embeds, trackers and all."""
        soup = BeautifulSoup(body, "html.parser")
        strip_scripts(soup)
        flatten_responsive_images(soup)
        if page_url:
            absolutize_refs(soup, page_url)
        return str(soup)

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        origin, locale = _api_base_and_locale(url)
        page_url: str | None = _articles_endpoint(url)

        articles: list[dict[str, Any]] = []
        pages = 0
        while page_url and pages < _MAX_PAGES:
            _f, body = http.fetch_text(page_url)
            data = json.loads(body)
            # The single-article endpoint returns one "article"; the list ones "articles".
            single = data.get("article")
            articles.extend(data.get("articles") or ([single] if single else []))
            page_url = data.get("next_page")
            pages += 1
            http.polite_sleep()
        truncated = bool(page_url)
        if truncated:
            log.warning("zendesk_help.capped", fetched=pages, cap=_MAX_PAGES)

        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        for i, art in enumerate(articles):
            title = _html.escape(art.get("title", ""))
            src = art.get("html_url", "")
            body = self._clean_body(art.get("body", "") or "", src)
            (raw_dir / f"{i:04d}.html").write_text(
                f"<!-- source: {src} -->\n<section>\n<h2>{title}</h2>\n{body}\n</section>\n",
                encoding="utf-8",
            )

        slug = _slug(url)
        # One article IS the deliverable, so pages=1 is correct here — not the
        # collapsed crawl audit's single_page_crawl check exists to catch.
        is_article = bool(_ARTICLE_RE.search(urlparse(url).path))
        log.info(
            "zendesk_help.acquire", origin=origin, locale=locale, articles=len(articles), slug=slug
        )
        return AcquireResult(
            raw_dir=raw_dir,
            kind="html",
            slug=slug,
            pages=len(articles),
            truncated=truncated,
            single_document=is_article,
        )

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        title = acq.slug.replace("-", " ").title()
        parts = [p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html"))]
        doc = (
            '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
            f"<title>{_html.escape(title)} Help</title></head>\n<body>\n"
            f"<h1>{_html.escape(title)} Help</h1>\n" + "\n".join(parts) + "\n</body></html>\n"
        )
        out = workdir / f"{acq.slug}.html"
        out.write_text(doc, encoding="utf-8")
        log.info("zendesk_help.normalize", slug=acq.slug, out=str(out), articles=len(parts))
        return out
