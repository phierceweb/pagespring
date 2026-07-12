"""zendesk_help — Zendesk Help Center sites (e.g. support.<vendor>.com/hc/...).

Uses the Help Center REST API (no scraping): ``/api/v2/help_center/<locale>/
articles.json`` is paginated and returns each article's title + HTML body.
acquire fetches all articles; normalize merges them into one HTML doc. Image
URLs in the bodies are absolute (Zendesk CDN) — pagespeak, or the optional
``--download-images``, handles them.

Point it at the help center, e.g. ``https://support.gingerlabs.com/hc/en-us``.
"""

from __future__ import annotations

import html as _html
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult

log = get_logger(__name__)

_MAX_PAGES = 100  # API pages (per_page=100) — safety cap


def _api_base_and_locale(url: str) -> tuple[str, str]:
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    m = re.search(r"/hc/([a-z]{2}-[a-z]{2})", p.path)
    return origin, (m.group(1) if m else "en-us")


def _slug(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.").removeprefix("support.")
    return re.sub(r"[^a-z0-9]+", "-", host.split(".")[0]).strip("-") or "help"


class ZendeskHelpPattern:
    name = "zendesk_help"
    convert_recipe = ["--split-sections"]

    def match(self, url: str) -> bool:
        p = urlparse(url)
        return p.netloc.lower().endswith(".zendesk.com") or "/hc/" in p.path

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        origin, locale = _api_base_and_locale(url)
        page_url: str | None = f"{origin}/api/v2/help_center/{locale}/articles.json?per_page=100"

        articles: list[dict[str, Any]] = []
        pages = 0
        while page_url and pages < _MAX_PAGES:
            _f, body = http.fetch_text(page_url)
            data = json.loads(body)
            articles.extend(data.get("articles", []))
            page_url = data.get("next_page")
            pages += 1
            http.polite_sleep()
        if page_url:
            log.warning("zendesk_help.capped", fetched=pages, cap=_MAX_PAGES)

        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        for i, art in enumerate(articles):
            title = _html.escape(art.get("title", ""))
            src = art.get("html_url", "")
            body = art.get("body", "") or ""
            (raw_dir / f"{i:04d}.html").write_text(
                f"<!-- source: {src} -->\n<section>\n<h2>{title}</h2>\n{body}\n</section>\n",
                encoding="utf-8",
            )

        slug = _slug(url)
        log.info(
            "zendesk_help.acquire", origin=origin, locale=locale, articles=len(articles), slug=slug
        )
        return AcquireResult(raw_dir=raw_dir, kind="html", slug=slug, pages=len(articles))

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
