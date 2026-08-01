"""adobe_helpx — Adobe AEM product guides (helpx.adobe.com/<product>/…).

The product is a path segment, and the guide entry
(``/<product>/user-guide.html``) carries the whole topic index as ``ul.tocList``
leaf links. No crawl — read the index, fetch each topic.

helpx emits no ``<meta name="generator">``, so it can only be claimed by host —
hence a top-level pattern rather than a docs_probe sub-module.
"""

from __future__ import annotations

import html as _html
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns._site import absolutize_refs, strip_scripts

log = get_logger(__name__)

_HOST = "helpx.adobe.com"
_MAX_PAGES = 3000
_CONTENT_CSS = "main"
# The TOC sidebar and a whole AEM component suite live INSIDE main, one set per
# topic. The CTA footer is a div.xf experience fragment, which the `footer`
# element selector does not reach.
_CHROME_CSS = (
    "div.sideNavigation, div.tocContainer, ul.tocList, "
    "div.contentcard, div.feedbackV2, div.pagenavigationarrows, div.socialmediashare, "
    "div.xfreference, div[id*=xfreference], nav, header, footer"
)
# robots.txt disallows this sibling product area; never follow a link into it.
_ROBOTS_DENY = ("/photoshop-elements-editor/", "/premiere-elements-editor/")


class AdobeHelpxPattern:
    name = "adobe_helpx"

    def match(self, url: str) -> bool:
        p = urlparse(url)
        if p.path.lower().endswith(".pdf"):
            return False  # helpx also serves PDFs; those belong to pdf_url
        segs = [s for s in p.path.split("/") if s]
        return p.netloc.lower() == _HOST and len(segs) >= 2

    def _product(self, url: str) -> str:
        segs = [s for s in urlparse(url).path.split("/") if s]
        return segs[0] if segs else "adobe"

    def _topic_urls(self, guide_html: str, guide_url: str, product: str) -> list[str]:
        """Every leaf topic in the guide's TOC, deduped, scoped to this product."""
        soup = BeautifulSoup(guide_html, "html.parser")
        seen: set[str] = set()
        out: list[str] = []
        for a in soup.select("ul.tocList a"):
            href = a.get("href")
            if not isinstance(href, str) or not href:
                continue
            full = urljoin(guide_url, href).split("#", 1)[0]
            path = urlparse(full).path
            if not path.endswith(".html") or not path.startswith(f"/{product}/"):
                continue  # a sibling product's guide is a different manual
            if any(deny in path for deny in _ROBOTS_DENY):
                continue
            if full not in seen:
                seen.add(full)
                out.append(full)
        return out

    def _extract(self, page_html: str, page_url: str) -> str | None:
        soup = BeautifulSoup(page_html, "html.parser")
        node = soup.select_one(_CONTENT_CSS)
        if not isinstance(node, Tag):
            return None
        for el in node.select(_CHROME_CSS):
            el.decompose()
        strip_scripts(node)
        absolutize_refs(node, page_url)
        return str(node)

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        product = self._product(url)
        guide_url = f"https://{_HOST}/{product}/user-guide.html"
        final_guide, guide_html = http.fetch_text(guide_url)
        topics = self._topic_urls(guide_html, final_guide, product)
        if not topics:
            raise InvalidInputError(
                f"{guide_url} lists no topics — helpx keeps the index in ul.tocList; "
                "the guide layout may have changed."
            )
        truncated = len(topics) > _MAX_PAGES
        if truncated:
            log.warning("adobe_helpx.capped", found=len(topics), cap=_MAX_PAGES)
            topics = topics[:_MAX_PAGES]

        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for i, topic in enumerate(topics):
            try:
                final, body = http.fetch_text(topic)
            except Exception as exc:
                log.warning("adobe_helpx.fetch_error", url=topic, error=str(exc))
                http.polite_sleep()
                continue
            fragment = self._extract(body, final)
            if fragment is None:
                log.warning("adobe_helpx.no_content", url=topic)
                http.polite_sleep()
                continue
            stem = urlparse(topic).path.strip("/").replace("/", "-").removesuffix(".html")
            (raw_dir / f"{i:04d}-{stem}.html").write_text(
                f"<!-- source: {topic} -->\n<section>\n{fragment}\n</section>\n", encoding="utf-8"
            )
            saved += 1
            http.polite_sleep()

        log.info("adobe_helpx.acquire", product=product, found=len(topics), pages=saved)
        return AcquireResult(
            raw_dir=raw_dir, kind="html", slug=product, pages=saved, truncated=truncated
        )

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        title = acq.title or f"{acq.slug.replace('-', ' ').title()} User Guide"
        parts = [p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html"))]
        doc = (
            '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
            f"<title>{_html.escape(title)}</title></head>\n<body>\n"
            f"<h1>{_html.escape(title)}</h1>\n" + "\n".join(parts) + "\n</body></html>\n"
        )
        out = workdir / f"{acq.slug}.html"
        out.write_text(doc, encoding="utf-8")
        log.info("adobe_helpx.normalize", slug=acq.slug, out=str(out), pages=len(parts))
        return out
