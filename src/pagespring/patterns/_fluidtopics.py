"""Fluid Topics portals for docs_probe: every URL serves one app shell, and the khub API it loads
serves a publication's TOC and topics; a reader URL names the publication by its pretty URL."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger
from pf_core.utils.hashing import content_hash
from pf_core.utils.slugify import slugify

from pagespring import http
from pagespring.base import IMAGES_DIR, AcquireResult
from pagespring.patterns._fluidtopics_page import ImageBundle, clean_topic
from pagespring.patterns._site import raw_stem
from pagespring.patterns._toc_stage import Section, TopicLost, stage_toc

log = get_logger(__name__)

_MAX_PAGES = 5000
_BASE_META = "ft-tenant-base-url"
_CLIENT_SCRIPT_RE = re.compile(r"/fluidtopicsclient\.nocache\.js(?:[?#]|$)", re.I)
_PAGE_SCRIPT_RE = re.compile(r"/fluidtopics(?:\.min)?\.js(?:[?#]|$)", re.I)
_APP_LOADER = "FT-application-loader"
_PAGE_SUFFIX_RE = re.compile(r"\.html?$", re.I)


@dataclass(frozen=True)
class _Publication:
    map_id: str
    title: str
    pretty: str | None
    version: str | None


@dataclass(frozen=True)
class _Topic:
    depth: int
    title: str
    content_id: str | None  # None for a title-only node, which heads the topics under it
    toc_id: str
    reader_url: str


def is_fluidtopics(page: str) -> bool:
    """True when the page is the Fluid Topics app shell: its tenant meta, its portal client, or its
    page script beside the app loader (the script alone could be an embed on another site)."""
    soup = BeautifulSoup(page, "html.parser")
    if soup.find("meta", attrs={"name": _BASE_META}) is not None:
        return True
    srcs = [s for tag in soup.find_all("script", src=True) if isinstance(s := tag.get("src"), str)]
    if any(_CLIENT_SCRIPT_RE.search(src) for src in srcs):
        return True
    return soup.find(id=_APP_LOADER) is not None and any(_PAGE_SCRIPT_RE.search(s) for s in srcs)


def tenant_base(page_url: str, page: str) -> str:
    """The portal's base URL as the shell declares it, else the page's origin."""
    meta = BeautifulSoup(page, "html.parser").find("meta", attrs={"name": _BASE_META})
    declared = meta.get("content") if meta is not None else None
    if isinstance(declared, str) and urlparse(declared).scheme in ("http", "https"):
        return declared if declared.endswith("/") else f"{declared}/"
    parts = urlparse(page_url)
    return f"{parts.scheme}://{parts.netloc}/"


def _json(url: str, what: str) -> Any:
    try:
        text = http.fetch_text(url)[1]
    except Exception as exc:
        raise InvalidInputError(f"{url} is not fetchable — {what} comes from there.") from exc
    try:
        return json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise InvalidInputError(f"{url} is not Fluid Topics JSON ({what})") from exc


def _meta(entry: dict[str, Any], key: str) -> str | None:
    for item in entry.get("metadata") or []:
        values = item.get("values") if isinstance(item, dict) else None
        if isinstance(item, dict) and item.get("key") == key and values:
            return str(values[0])
    return None


def _from_map(entry: dict[str, Any]) -> _Publication:
    return _Publication(
        str(entry["id"]),
        str(entry.get("title") or ""),
        _meta(entry, "ft:prettyUrl"),
        _meta(entry, "version"),
    )


def _seed_segments(base: str, seed_url: str) -> list[str]:
    path, base_path = urlparse(seed_url).path, urlparse(base).path
    rest = path[len(base_path) :] if path.startswith(base_path) else path
    return [seg for seg in rest.split("/") if seg]


def _decoded_path(url: str) -> str:
    return unquote(urlparse(url).path).rstrip("/")


def _publication(base: str, seed_url: str) -> _Publication:
    """The publication a reader URL names: ``/reader/<map>/…`` directly, ``/r/<pretty>/…`` by the
    longest pretty URL its path starts with."""
    segs = _seed_segments(base, seed_url)
    if len(segs) >= 2 and segs[0] == "reader":
        found = _json(f"{base}api/khub/maps/{segs[1]}", "the publication")
        if isinstance(found, dict) and found.get("id"):
            return _from_map(found)
    elif len(segs) >= 2 and segs[0] == "r":
        maps = _json(f"{base}api/khub/maps", "the portal's publication list")
        wanted = [unquote(seg) for seg in segs[1:]]
        best: tuple[int, dict[str, Any]] | None = None
        for entry in maps if isinstance(maps, list) else []:
            pretty = _meta(entry, "ft:prettyUrl") if isinstance(entry, dict) else None
            names = [unquote(seg) for seg in (pretty or "").split("/") if seg]
            if names and wanted[: len(names)] == names and (best is None or len(names) > best[0]):
                best = (len(names), entry)
        if best is not None and best[1].get("id"):
            return _from_map(best[1])
    raise InvalidInputError(
        f"{seed_url} names no publication on {base} — point at a publication's reader URL "
        "(/r/<publication>/…)."
    )


def _reader_url(base: str, pretty: str) -> str:
    """A TOC node's pretty URL, rooted at the host only when it names the tenant's own path."""
    if pretty.startswith("/") and not pretty.startswith(urlparse(base).path):
        pretty = pretty.lstrip("/")
    return urljoin(base, pretty)


def _topics(base: str, pub: _Publication) -> list[_Topic]:
    url = f"{base}api/khub/maps/{pub.map_id}/toc"
    tree = _json(url, "the publication's TOC")
    if not isinstance(tree, list):
        raise InvalidInputError(f"{url} is not a Fluid Topics TOC")

    def walk(nodes: list[Any], depth: int) -> list[_Topic]:
        out: list[_Topic] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            kids = node.get("children")
            below = walk(kids, depth + 1) if isinstance(kids, list) else []
            content, toc = node.get("contentId"), node.get("tocId")
            title = str(node.get("title") or "")
            if isinstance(content, str) and content and isinstance(toc, str) and toc:
                pretty = node.get("prettyUrl")
                reader = _reader_url(base, pretty) if isinstance(pretty, str) and pretty else ""
                reader = reader or f"{base}reader/{pub.map_id}/{toc}"
                out.append(_Topic(depth, title, content, toc, reader))
            elif title and any(topic.content_id for topic in below):
                out.append(_Topic(depth, title, None, "", ""))
            out.extend(below)
        return out

    try:
        found = walk(tree, 0)
    except RecursionError as exc:
        raise InvalidInputError(f"{url} nests too deep to be a Fluid Topics TOC") from exc
    if not any(topic.content_id for topic in found):
        raise InvalidInputError(f"{url} lists no topics")
    return found


def _check_seed(base: str, seed_url: str, pub: _Publication, topics: list[_Topic]) -> None:
    """Refuse a ``/r/`` seed past its publication's root that names none of its topics: the longest
    pretty-URL match was then a shorter publication (an umbrella), not the one asked for."""
    if _seed_segments(base, seed_url)[:1] != ["r"] or pub.pretty is None:
        return
    seed = _decoded_path(seed_url)
    if seed == _decoded_path(urljoin(base, f"r/{pub.pretty}")):
        return
    if any(_decoded_path(topic.reader_url) == seed for topic in topics if topic.content_id):
        return
    raise InvalidInputError(
        f"{seed_url} names no topic of {pub.title or pub.map_id} on {base} — point at a "
        "publication's reader URL (/r/<publication>/…)."
    )


def _identity(slug: str, pub: _Publication) -> tuple[str, str | None]:
    """One portal holds many publications and versions; the pretty URL tells them apart."""
    named = slugify(f"{slug}-{pub.pretty or pub.map_id}") or slug
    title = f"{pub.title} {pub.version}" if pub.title and pub.version else pub.title
    return named, title or None


def acquire(base: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    seed_url, seed = http.fetch_text(base)
    tenant = tenant_base(seed_url, seed)
    http.polite_sleep()
    pub = _publication(tenant, seed_url)
    http.polite_sleep()
    topics = _topics(tenant, pub)
    _check_seed(tenant, seed_url, pub, topics)
    slug, title = _identity(slug, pub)
    links = {topic.toc_id: topic.reader_url for topic in topics if topic.content_id}
    # Content reused under another title is staged under each; the TOC is all that names it.
    keys = {(topic.content_id, topic.title) for topic in topics if topic.content_id}
    truncated = len(keys) > _MAX_PAGES
    if truncated:
        log.warning("fluidtopics.capped", found=len(topics), cap=_MAX_PAGES)
    prefix = urljoin(tenant, f"r/{pub.pretty}/") if pub.pretty else ""

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    bundle = ImageBundle(raw_dir / IMAGES_DIR)

    def fetch(topic: _Topic) -> Section | None:
        url = f"{tenant}api/khub/maps/{pub.map_id}/topics/{topic.content_id}/content"
        try:
            body = http.fetch_text(url)[1]
        except Exception as exc:
            raise TopicLost(f"{url}: {exc}") from exc
        node = clean_topic(
            body,
            page_url=topic.reader_url,
            title=topic.title,
            depth=topic.depth,
            links=links,
            bundle=bundle,
        )
        if node is None:
            return None
        # The body carries no title, so a body alone would merge distinct topics.
        digest = content_hash(json.dumps([topic.title, body]))
        tail = topic.reader_url.removeprefix(prefix) if prefix else topic.toc_id
        stem = raw_stem(_PAGE_SUFFIX_RE.sub("", tail))
        return Section(str(node), digest, topic.reader_url, stem)

    staged = stage_toc(
        topics,
        raw_dir,
        key=lambda topic: (topic.content_id, topic.title) if topic.content_id else None,
        fetch=fetch,
        max_pages=_MAX_PAGES,
        event="fluidtopics",
    )

    log.info(
        "fluidtopics.acquire", map=pub.map_id, found=len(topics), pages=staged.pages, slug=slug
    )
    return AcquireResult(
        raw_dir=raw_dir,
        kind="html",
        slug=slug,
        pages=staged.pages,
        title=title,
        truncated=truncated or staged.stalled,
        lost=staged.lost,
    )
