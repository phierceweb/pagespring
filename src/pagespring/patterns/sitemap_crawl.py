"""sitemap_crawl: an opt-in crawl, matched only by a URL naming a sitemap file, of the listed pages
under its directory, each reduced to main content. Every locale it lists is staged."""

from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger

from pagespring.base import AcquireResult
from pagespring.patterns import _readable, _sitemap
from pagespring.patterns._site import slug_from_host

log = get_logger(__name__)

_MAX_PAGES = 2000
_SITEMAP_NAME_RE = re.compile(r"^sitemap[\w.-]*\.xml$", re.I)


def _site_name(page: str | None) -> str | None:
    """The site's name as the page declares it (``og:site_name``), or None."""
    if page is None:
        return None
    meta = BeautifulSoup(page, "html.parser").find("meta", attrs={"property": "og:site_name"})
    content = meta.get("content") if isinstance(meta, Tag) else None
    return content.strip() or None if isinstance(content, str) else None


def _slug(base: str) -> str:
    p = urlparse(base)
    return "-".join([slug_from_host(p.netloc), *(s for s in p.path.split("/") if s)])


class SitemapCrawlPattern:
    name = "sitemap_crawl"

    def match(self, url: str) -> bool:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(
            _SITEMAP_NAME_RE.match(p.path.rsplit("/", 1)[-1])
        )

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        final, locs, child_failed = _sitemap.read_locs(url)
        base = _sitemap.directory(final)
        pages = [
            u
            for u in _sitemap.unique(locs)
            if _sitemap.under(u, base) and not _sitemap.is_error_page(u)
        ]
        if not pages:
            raise InvalidInputError(f"{final} lists no page under {base}")
        truncated = child_failed or len(pages) > _MAX_PAGES
        if len(pages) > _MAX_PAGES:
            log.warning("sitemap_crawl.capped", found=len(pages), cap=_MAX_PAGES)
            pages = pages[:_MAX_PAGES]

        crawl = _sitemap.crawl(
            pages,
            workdir / "raw",
            extract=_readable.extract_main,
            in_scope=lambda u: _sitemap.under(u, base),
            event="sitemap_crawl",
        )
        if crawl.saved == 0:
            raise InvalidInputError(
                f"none of the {len(pages)} pages {final} lists under {base} has readable content"
            )
        slug = _slug(base)
        log.info(
            "sitemap_crawl.acquire",
            sitemap=final,
            base=base,
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
            title=_site_name(crawl.first_page),
            truncated=truncated or crawl.stalled,
            lost=crawl.lost,
        )

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        fragments = [f.read_text(encoding="utf-8") for f in sorted(acq.raw_dir.glob("*.html"))]
        out = workdir / f"{acq.slug}.html"
        if not fragments:
            # Zero bytes trips EmptyOutputError before staging clears a prior deliverable.
            out.write_text("", encoding="utf-8")
        else:
            title = html.escape(acq.title or acq.slug)
            out.write_text(
                "<!DOCTYPE html>\n"
                f'<html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>\n'
                "<body>\n" + "\n".join(fragments) + "\n</body>\n</html>\n",
                encoding="utf-8",
            )
        log.info("sitemap_crawl.normalize", slug=acq.slug, out=str(out), pages=len(fragments))
        return out
