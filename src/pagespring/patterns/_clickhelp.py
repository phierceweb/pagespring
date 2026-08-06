"""ClickHelp acquisition for docs_probe — TOC-as-data, no crawl.

ClickHelp's webHelp export ships **no** ``<meta name="generator">``, so the meta
sniff can never claim it; the tells are its own asset paths and body class.

The entire page index is one file — ``<root>/_webHelpScripts/Master/toc_nav.js``
— holding the TOC as a JS array of nodes plus the topic URL template. Reading it
replaces a crawl: every topic id is known up front.

Per topic: keep ``#pnlTopicContentContainer``, strip the chrome that sits
*inside* it (the footer and the Next link are within the container, not around
it), and absolutize the ``../Storage/...`` asset refs.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger
from pf_core.utils.slugify import slugify

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns._site import absolutize_refs, flatten_responsive_images, strip_scripts

log = get_logger(__name__)

_MAX_TOPICS = 5000
_TOC_PATH = "_webHelpScripts/Master/toc_nav.js"
_CONTENT_ID = "pnlTopicContentContainer"
# Chrome rendered INSIDE the content container. Match the whole pager/mini-TOC
# family by prefix: naming only the one variant visible on the page you happen
# to inspect leaves the siblings behind on every topic.
_INNER_CHROME_CSS = (
    "div.footer, div.CHBreadcrumbs, "
    "[class^=CHNavLink], [class*=' CHNavLink'], "
    "[class^=CHMiniToc], [class*=' CHMiniToc']"
)
_TELLS = ("CHWebHelp.css", "WebHelp_body", "_webHelpScripts/")
_EXTERNAL_ID_RE = re.compile(r'"e"\s*:\s*"([^"]+)"')


def is_clickhelp(html: str) -> bool:
    """True when the page carries ClickHelp's own asset/body tells."""
    return sum(tell in html for tell in _TELLS) >= 2


def manual_root(url: str) -> str:
    """The publication root: topics live at ``<root>/HTML/<id>.html``."""
    base = url.rsplit("/", 1)[0]  # drop <id>.html
    return base[: -len("/HTML")] if base.endswith("/HTML") else base


def slug_from_path(url: str) -> str:
    """Slug from the publication directory, never the host.

    One vendor serves many manuals from one host, so a host-derived slug
    collides across their products.
    """
    segs = [s for s in urlparse(manual_root(url)).path.split("/") if s]
    for seg in reversed(segs):
        if seg.lower() not in {"manual", "manuals", "html", "docs"}:
            return slugify(seg) or "manual"
    return "manual"


def _topic_ids(toc_js: str) -> list[str]:
    """Topic external-ids in TOC order, deduped."""
    seen: set[str] = set()
    out: list[str] = []
    for tid in _EXTERNAL_ID_RE.findall(toc_js):
        if tid not in seen:
            seen.add(tid)
            out.append(tid)
    return out


def _extract(page_html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(page_html, "html.parser")
    node = soup.find(id=_CONTENT_ID)
    if not isinstance(node, Tag):
        return None
    for el in node.select(_INNER_CHROME_CSS):
        el.decompose()
    strip_scripts(node)
    flatten_responsive_images(node)
    absolutize_refs(node, page_url)
    return str(node)


def acquire(url: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    root = manual_root(url)
    toc_url = f"{root}/{_TOC_PATH}"
    try:
        _f, toc_js = http.fetch_text(toc_url)
    except Exception as exc:
        raise InvalidInputError(
            f"{toc_url} is not fetchable — a ClickHelp export publishes its whole "
            "index in toc_nav.js; without it the topics cannot be enumerated."
        ) from exc

    ids = _topic_ids(toc_js)
    if not ids:
        raise InvalidInputError(f"{toc_url} lists no topics — not a ClickHelp TOC?")
    truncated = len(ids) > _MAX_TOPICS
    if truncated:
        log.warning("clickhelp.capped", found=len(ids), cap=_MAX_TOPICS)
        ids = ids[:_MAX_TOPICS]

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    lost = 0
    for i, tid in enumerate(ids):
        page_url = f"{root}/HTML/{tid}.html"
        try:
            final, body = http.fetch_text(page_url)
        except Exception as exc:
            lost += 1
            log.warning("clickhelp.fetch_error", url=page_url, error=str(exc))
            http.polite_sleep()
            continue
        fragment = _extract(body, final)
        if fragment is None:
            lost += 1
            log.warning("clickhelp.no_container", url=page_url)
            http.polite_sleep()
            continue
        # tid is remote-controlled (toc_nav.js); flatten only here — the URL above
        # needs it verbatim.
        stem = tid.strip("/").replace("/", "-") or "topic"
        (raw_dir / f"{i:04d}-{stem}.html").write_text(
            f"<!-- source: {page_url} -->\n<section>\n{fragment}\n</section>\n", encoding="utf-8"
        )
        saved += 1
        http.polite_sleep()

    log.info("clickhelp.acquire", root=root, topics=len(ids), pages=saved, slug=slug)
    return AcquireResult(
        raw_dir=raw_dir,
        kind="html",
        slug=slug,
        pages=saved,
        title=title,
        truncated=truncated,
        lost=lost,
    )
