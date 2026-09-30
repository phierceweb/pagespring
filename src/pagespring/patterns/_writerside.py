"""JetBrains Writerside acquisition for docs_probe — HelpTOC.json-driven, no crawl.

Every Writerside help instance publishes ``HelpTOC.json`` beside its topics: the
full navigation tree, in reading order. Topics are flat files in one directory.
The entry page a reader lands on is often a client-rendered starting page with
no content or links at all, so discovery comes from the TOC alone. Each topic
lands at its TOC depth: groups become headings, and a topic's own headings shift
below them. Page cleanup lives in ``_writerside_page``.
"""

from __future__ import annotations

import html
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger
from pf_core.utils.hashing import content_hash
from pf_core.utils.slugify import slugify

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.config import cfg
from pagespring.liveness import ProgressWatchdog
from pagespring.patterns._site import names_a_file, raw_stem
from pagespring.patterns._writerside_page import MAX_HEADING, LabelNames, extract, shift_headings

log = get_logger(__name__)

_MAX_PAGES = 5000
_TOC_FILE = "HelpTOC.json"
_GENERIC_DIRS = {"doc", "docs", "documentation", "help", "manual", "manuals"}


@dataclass(frozen=True)
class _Entry:
    depth: int
    title: str
    url: str | None  # None for a group, or for a link that leaves the instance


def is_writerside(page: str) -> bool:
    """True when the page carries the help app's own hooks: the in-page TOC data
    block and the template the client renders into."""
    soup = BeautifulSoup(page, "html.parser")
    body = soup.body
    return (
        soup.find("script", id="virtual-toc-data") is not None
        and isinstance(body, Tag)
        and body.has_attr("data-template")
    )


def _instance_dir(url: str) -> str:
    p = urlparse(url.split("#", 1)[0].split("?", 1)[0])
    segs = [s for s in p.path.split("/") if s]
    if segs and names_a_file(segs[-1]):
        segs.pop()
    return f"{p.scheme}://{p.netloc}/" + "".join(f"{s}/" for s in segs)


def _topic_url(href: object, instance: str) -> str | None:
    """The topic's absolute URL, or None for a link that leaves the instance.

    Topics are relative file names; an absolute or rooted href is an external
    link, even one that lands on the same host."""
    if not isinstance(href, str) or not href or urlparse(href).scheme or href.startswith("/"):
        return None
    url = urldefrag(urljoin(instance, href)).url
    rest = url[len(instance) :] if url.startswith(instance) else ""
    return url if rest and "/" not in rest else None


def _toc_entries(raw: str, instance: str) -> list[_Entry]:
    try:
        toc = json.loads(raw)
        nodes: dict[str, Any] = toc["entities"]["pages"]
        top: list[Any] = toc["topLevelIds"]
    except (ValueError, TypeError, KeyError) as exc:
        raise InvalidInputError(f"{instance}{_TOC_FILE} is not a Writerside TOC") from exc
    if not isinstance(nodes, dict) or not nodes or not isinstance(top, list):
        raise InvalidInputError(f"{instance}{_TOC_FILE} lists no topics")

    visited: set[str] = set()

    def walk(ids: list[Any], depth: int) -> list[_Entry]:
        out: list[_Entry] = []
        for node_id in ids:
            node = nodes.get(node_id) if isinstance(node_id, str) else None
            if not isinstance(node, dict) or node_id in visited:
                continue
            visited.add(node_id)
            kids = node.get("pages")
            below = walk(kids, depth + 1) if isinstance(kids, list) else []
            url = _topic_url(node.get("url"), instance)
            # A group whose topics all live outside the instance would head nothing.
            if url is not None or any(e.url is not None for e in below):
                out.append(_Entry(depth, str(node.get("title") or ""), url))
            out.extend(below)
        return out

    return walk(top, 0)


def _heading(entry: _Entry) -> str:
    level = min(entry.depth + 1, MAX_HEADING)
    return f"<h{level}>{html.escape(entry.title)}</h{level}>"


def _identity(instance: str, slug: str, title: str | None) -> tuple[str, str | None]:
    """The slug extended by a non-generic instance directory, and the manual's
    name from the ``<topic> | <manual>`` page title."""
    segs = [s for s in urlparse(instance).path.split("/") if s]
    last = segs[-1].lower() if segs else ""
    if last and last not in _GENERIC_DIRS and slugify(last) != slug:
        slug = slugify(f"{slug}-{last}") or slug
    if title and " | " in title:
        title = title.rsplit(" | ", 1)[1]
    return slug, " ".join(title.split()) if title else title


def acquire(base: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    instance = _instance_dir(base)
    toc_url = f"{instance}{_TOC_FILE}"
    try:
        _final, raw = http.fetch_text(toc_url)
    except Exception as exc:
        raise InvalidInputError(
            f"{toc_url} is not fetchable — a Writerside instance lists its topics there."
        ) from exc
    entries = _toc_entries(raw, instance)
    slug, title = _identity(instance, slug, title)
    topics = list(dict.fromkeys(e.url for e in entries if e.url is not None))
    truncated = len(topics) > _MAX_PAGES
    if truncated:
        log.warning("writerside.capped", found=len(topics), cap=_MAX_PAGES)

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    pending: list[str] = []
    fetched: set[str] = set()
    digests: set[str] = set()
    labels = LabelNames(instance)
    saved = 0
    lost = 0
    watchdog = ProgressWatchdog(stall_after_s=cfg.CRAWL_STALL_AFTER_S, now=time.monotonic)
    for entry in entries:
        if entry.url is None:
            pending.append(_heading(entry))
            continue
        if entry.url in fetched:
            continue
        if len(fetched) >= _MAX_PAGES:
            break
        if watchdog.stalled():
            log.warning("writerside.stalled", saved=saved, idle_s=round(watchdog.idle_s()))
            truncated = True
            break
        fetched.add(entry.url)
        http.polite_sleep()
        try:
            final, body = http.fetch_text(entry.url)
        except Exception as exc:
            lost += 1
            log.warning("writerside.fetch_error", url=entry.url, error=str(exc))
            continue
        node = (
            extract(body, final, title=entry.title, labels=labels)
            if _instance_dir(final) == instance
            else None
        )
        if node is None:
            lost += 1
            log.warning("writerside.no_article", url=entry.url, final=final)
            continue
        digest = content_hash(str(node))
        if digest in digests:
            log.info("writerside.duplicate_page", url=entry.url)
            continue
        digests.add(digest)
        shift_headings(node, entry.depth)
        stem = raw_stem(entry.url[len(instance) :].removesuffix(".html"))
        (raw_dir / f"{saved:04d}-{stem}.html").write_text(
            f"<!-- source: {entry.url} -->\n<section>\n{''.join(pending)}\n{node}\n</section>\n",
            encoding="utf-8",
        )
        pending.clear()
        saved += 1
        watchdog.progress()

    log.info("writerside.acquire", toc=toc_url, found=len(topics), pages=saved, slug=slug)
    return AcquireResult(
        raw_dir=raw_dir,
        kind="html",
        slug=slug,
        pages=saved,
        title=title,
        truncated=truncated,
        lost=lost,
    )
