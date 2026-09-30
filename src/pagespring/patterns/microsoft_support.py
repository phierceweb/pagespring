"""microsoft_support — Microsoft 365 end-user help (support.microsoft.com).

acquire finds the product's article catalog via the per-product sitemap
(``/_sitemaps/<product>_<locale>_<n>.xml``; the hub page server-renders only a
fraction of the catalog). When no product sitemap exists it falls back to
scraping the hub's ``/office/`` links. Each article's
``<div class="learnArticleContent">`` body is extracted (title from the page
``<h1>``); title-less chrome shells are skipped. normalize merges
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
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns._site import absolutize_refs, flatten_responsive_images

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
_URLSET_RE = re.compile(r"<urlset\b")
_MAX = 2000  # above the largest per-product sitemap
_MAX_SITEMAP_PAGES = 50  # the largest product publishes a single sitemap page
_MIN_BODY = 200  # below this, a title-less page is a chrome shell — skip it
_PACE = 1.0  # seconds between requests — the site quota-blocks bursts with 403s
_COOLDOWN = 60.0  # seconds to back off when the site throttles (it 403s, not 429s)
_MAX_FAILED_COOLDOWNS = 3  # consecutive failed retries → sustained block; the crawl stops


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
    flatten_responsive_images(body)
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


def _sitemap_articles(product: str, locale: str) -> tuple[list[str], bool]:
    """Article URLs from the per-product sitemap pages (…_1.xml, _2.xml, …), and
    whether enumeration ended early; empty list when the product has no sitemap
    (caller falls back to the hub).

    The abort has to travel: the articles it cost were never discovered, so they
    can't be counted one by one the way a failed page fetch can.
    """
    links: list[str] = []
    for n in range(1, _MAX_SITEMAP_PAGES + 1):
        url = _SITEMAP_TPL.format(product=product, locale=locale, n=n)
        try:
            _f, xml = http.fetch_text(url)
        except urllib.error.HTTPError as exc:
            # 404 is the expected end of pagination; any other status (e.g. a 403
            # throttle mid-crawl) stopped enumeration early and silently truncated the catalog.
            if exc.code != 404:
                log.warning(
                    "microsoft_support.sitemap_error", url=url, status=exc.code, pages=n - 1
                )
                return links, True
            break
        except Exception as exc:  # network/timeout mid-crawl — truncation, not the end
            log.warning("microsoft_support.sitemap_error", url=url, error=str(exc), pages=n - 1)
            return links, True
        finally:
            http.polite_sleep(_PACE)
        found = _LOC_RE.findall(xml)
        if not found:
            if _URLSET_RE.search(xml):
                break
            # A WAF interstitial or CDN error page answers 200; the catalog continues past it.
            log.warning("microsoft_support.sitemap_not_xml", url=url, pages=n - 1)
            return links, True
        links.extend(found)
    else:
        log.warning("microsoft_support.sitemap_capped", product=product, cap=_MAX_SITEMAP_PAGES)
        return links, True
    return links, False


def _blocked(probe: str) -> bool:
    """Whether ``probe`` is refused now."""
    try:
        http.fetch_text(probe)
    except urllib.error.HTTPError as exc:
        return exc.code == 403
    except Exception:
        return False
    finally:
        http.polite_sleep(_PACE)
    return False


class MicrosoftSupportPattern:
    name = "microsoft_support"

    def match(self, url: str) -> bool:
        return urlparse(url).netloc.lower() == "support.microsoft.com"

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        slug = _slug(url)
        links, sitemap_aborted = _sitemap_articles(slug, _locale(url))
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

        truncated = sitemap_aborted or len(links) > _MAX
        if len(links) > _MAX:
            log.warning("microsoft_support.capped", found=len(links), cap=_MAX)

        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        lost = 0
        failed_cooldowns = 0  # consecutive cooldown-retries that still 403'd
        last_good: str | None = None
        todo = links[:_MAX]
        for i, link in enumerate(todo):
            # A retired or restricted article also answers 403, so a block counts only
            # once an article that loaded before — or, with none yet, this one — is refused.
            if failed_cooldowns >= _MAX_FAILED_COOLDOWNS and _blocked(last_good or link):
                lost += len(todo) - i
                log.warning("microsoft_support.blocked", unfetched=len(todo) - i, lost=lost)
                break
            failed_cooldowns = min(failed_cooldowns, _MAX_FAILED_COOLDOWNS - 1)
            try:
                try:
                    _ff, art = http.fetch_text(link)
                except urllib.error.HTTPError as exc:
                    if exc.code != 403:
                        raise
                    # The site throttles with 403: cool down, then retry once.
                    log.warning("microsoft_support.throttled", url=link, cooldown=_COOLDOWN)
                    http.polite_sleep(_COOLDOWN)
                    try:
                        _ff, art = http.fetch_text(link)
                    except urllib.error.HTTPError:
                        failed_cooldowns += 1
                        raise
                failed_cooldowns = 0
                last_good = link
                title, body = _title_and_body(art, link)
            except Exception as exc:
                lost += 1
                log.warning("microsoft_support.fetch_error", url=link, error=str(exc))
                continue
            finally:
                http.polite_sleep(_PACE)
            if body is None:
                lost += 1
                log.warning("microsoft_support.no_content", url=link)
                continue
            if not title and len(body) < _MIN_BODY:
                continue  # a title-less chrome shell scraped off the hub, not an article
            if not title:
                title = link.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").capitalize()
            (raw_dir / f"{i:04d}.html").write_text(
                f"<!-- source: {link} -->\n<section>\n<h2>{_html.escape(title)}</h2>\n{body}\n</section>\n",
                encoding="utf-8",
            )
            saved += 1

        log.info("microsoft_support.acquire", url=url, mode=mode, articles=saved, slug=slug)
        return AcquireResult(
            raw_dir=raw_dir,
            kind="html",
            slug=slug,
            pages=saved,
            truncated=truncated,
            lost=lost,
        )

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        title = acq.slug.replace("-", " ").title()
        parts = [p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html"))]
        if not parts:
            # The wrapper alone is non-empty, so staging would accept it and clear
            # the good deliverable it replaces.
            raise InvalidInputError(
                f"{acq.slug}: the crawl captured no article — the hub's shape changed, "
                "or the site quota-blocked every fetch. Nothing was staged."
            )
        doc = (
            '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
            f"<title>{_html.escape(title)} Help</title></head>\n<body>\n"
            f"<h1>{_html.escape(title)} Help</h1>\n" + "\n".join(parts) + "\n</body></html>\n"
        )
        out = workdir / f"{acq.slug}.html"
        out.write_text(doc, encoding="utf-8")
        log.info("microsoft_support.normalize", slug=acq.slug, out=str(out), articles=len(parts))
        return out
