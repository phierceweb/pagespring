"""microsoft_support — Microsoft 365 end-user help (support.microsoft.com).

acquire finds the product's article catalog via the per-product sitemap
(``/_sitemaps/<product>_<locale>_<n>.xml`` — e.g. excel_en-us_1.xml lists
~1700 articles; the hub page server-renders only ~24). When no product
sitemap exists it falls back to scraping the hub's ``/office/`` links. Each
article's ``<div class="learnArticleContent">`` body is extracted (title from
the page ``<h1>``); title-less chrome shells are skipped. normalize merges
them. Image URLs are absolute (``--download-images`` localizes them).

Point it at an app hub, e.g. ``https://support.microsoft.com/en-us/excel``.
"""

from __future__ import annotations

import html as _html
import re
import urllib.error
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns._site import absolutize_refs

log = get_logger(__name__)

_PARSER = "html.parser"
_ARTICLE_RE = re.compile(r"/[a-z]{2}-[a-z]{2}/office/[A-Za-z0-9._-]+")
_CHROME_RE = re.compile(
    "feedback|wasThisHelpful|articleExperience|supExternalSurvey|supLeftNav|leftNav"
    "|ocpRelated|relatedTopics|breadcrumb|supMultimedia|supTOC",
    re.IGNORECASE,
)
_SITEMAP_TPL = "https://support.microsoft.com/_sitemaps/{product}_{locale}_{n}.xml"
_LOC_RE = re.compile(r"<loc>([^<]+)</loc>")
_MAX = 2000  # per-product sitemap scale (excel ≈ 1700)
_MIN_BODY = 200  # below this, a title-less page is a chrome shell — skip it
_COOLDOWN = 60.0  # seconds to back off when the site throttles (it 403s, not 429s)
_MAX_FAILED_COOLDOWNS = 3  # consecutive failed retries → sustained block; stop paying cooldowns


def _title_and_body(page_html: str, page_url: str) -> tuple[str | None, str | None]:
    soup = BeautifulSoup(page_html, _PARSER)
    body = (
        soup.find(class_="learnArticleContent")
        or soup.find(id="ocMainContent")
        or soup.find(id="ocArticle")
    )
    if body is None:
        return None, None
    for tag in body.find_all(["script", "style", "button", "nav"]):
        tag.decompose()
    for junk in body.find_all(class_=_CHROME_RE):
        junk.decompose()
    for junk in body.find_all(id=_CHROME_RE):
        junk.decompose()
    absolutize_refs(body, page_url)  # articles serve relative media/ paths
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    return title, body.decode_contents()


def _slug(url: str) -> str:
    parts = [
        p for p in urlparse(url).path.split("/") if p and not re.fullmatch(r"[a-z]{2}-[a-z]{2}", p)
    ]
    return parts[-1] if parts else "microsoft"


def _locale(url: str) -> str:
    m = re.search(r"/([a-z]{2}-[a-z]{2})(?:/|$)", urlparse(url).path)
    return m.group(1) if m else "en-us"


def _sitemap_articles(product: str, locale: str) -> list[str]:
    """Article URLs from the per-product sitemap pages (…_1.xml, _2.xml, …);
    empty list when the product has no sitemap (caller falls back to the hub)."""
    links: list[str] = []
    n = 1
    while True:
        url = _SITEMAP_TPL.format(product=product, locale=locale, n=n)
        try:
            _f, xml = http.fetch_text(url)
        except urllib.error.HTTPError as exc:
            # 404 is the expected end of pagination; any other status (e.g. a 403
            # throttle mid-crawl) stopped us early and silently truncated the catalog.
            if exc.code != 404:
                log.warning(
                    "microsoft_support.sitemap_error", url=url, status=exc.code, pages=n - 1
                )
            break
        except Exception as exc:  # network/timeout mid-crawl — truncation, not the end
            log.warning("microsoft_support.sitemap_error", url=url, error=str(exc), pages=n - 1)
            break
        links.extend(_LOC_RE.findall(xml))
        n += 1
    return links


class MicrosoftSupportPattern:
    name = "microsoft_support"
    convert_recipe = ["--split-sections"]

    def match(self, url: str) -> bool:
        return urlparse(url).netloc.lower() == "support.microsoft.com"

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        slug = _slug(url)
        links = _sitemap_articles(slug, _locale(url))
        mode = "sitemap"
        if not links:
            mode = "hub"  # no per-product sitemap — scrape the hub's links
            _f, hub = http.fetch_text(url)
            seen: set[str] = set()
            for path in _ARTICLE_RE.findall(hub):
                full = urljoin(url, path)
                if full not in seen:
                    seen.add(full)
                    links.append(full)

        if len(links) > _MAX:
            log.warning("microsoft_support.capped", found=len(links), cap=_MAX)

        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        failed_cooldowns = 0  # consecutive cooldown-retries that still 403'd
        for i, link in enumerate(links[:_MAX]):
            try:
                try:
                    _ff, art = http.fetch_text(link)
                except urllib.error.HTTPError as exc:
                    if exc.code != 403 or failed_cooldowns >= _MAX_FAILED_COOLDOWNS:
                        raise
                    # The site throttles with 403: cool down, then retry once.
                    log.warning("microsoft_support.throttled", url=link, cooldown=_COOLDOWN)
                    http.polite_sleep(_COOLDOWN)
                    try:
                        _ff, art = http.fetch_text(link)
                    except urllib.error.HTTPError:
                        failed_cooldowns += 1
                        raise
                    failed_cooldowns = 0  # recovered — the block was a burst
                title, body = _title_and_body(art, link)
            except Exception as exc:
                log.warning("microsoft_support.fetch_error", url=link, error=str(exc))
                continue
            if body is None or (not title and len(body) < _MIN_BODY):
                continue  # no content div, or a title-less chrome shell
            if not title:
                title = link.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").capitalize()
            (raw_dir / f"{i:04d}.html").write_text(
                f"<!-- source: {link} -->\n<section>\n<h2>{_html.escape(title)}</h2>\n{body}\n</section>\n",
                encoding="utf-8",
            )
            saved += 1
            http.polite_sleep(1.0)  # gentle pace — the site quota-blocks bursts with 403s

        log.info("microsoft_support.acquire", url=url, mode=mode, articles=saved, slug=slug)
        return AcquireResult(raw_dir=raw_dir, kind="html", slug=slug, pages=saved)

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
        log.info("microsoft_support.normalize", slug=acq.slug, out=str(out), articles=len(parts))
        return out
