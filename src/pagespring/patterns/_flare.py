"""MadCap Flare HTML5 help for docs_probe: no generator meta, but ``data-mc-*`` runtime attributes
on every ``<html>``; ``Data/HelpSystem.xml`` names the TOC data that lists the topics, no crawl."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger
from pf_core.utils.hashing import content_hash
from pf_core.utils.slugify import slugify

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns._flare_page import extract
from pagespring.patterns._flare_toc import TocEntry, read_chunk, read_toc, toc_entries
from pagespring.patterns._site import names_a_file, raw_stem, seat_headings
from pagespring.patterns._toc_stage import Section, TopicLost, stage_toc

log = get_logger(__name__)

_MAX_PAGES = 5000
_HELP_SYSTEM = "Data/HelpSystem.xml"
_RUNTIME = "data-mc-runtime-file-type"
_PATH_TO_ROOT = "data-mc-path-to-help-system"
_RUNTIME_ATTRS = (_RUNTIME, _PATH_TO_ROOT)
_PAGE_SUFFIX_RE = re.compile(r"\.html?$", re.I)
_TOC_SUFFIX_RE = re.compile(r"[-_ ]?toc$", re.I)
_LOCALE_RE = re.compile(r"^[a-z]{2}(?:[-_][a-z]{2,4})?$", re.I)
# Flare's own default names, and directory names any manual could carry.
_GENERIC_NAMES = {
    "default", "index", "home", "master", "main", "start", "toc", "output", "webhelp",
    "help", "online", "content", "doc", "docs", "documentation", "manual", "manuals",
    "guide", "userguide", "user_guide", "user-guide",
}  # fmt: skip


def _html_tag(page: str) -> Tag | None:
    found = BeautifulSoup(page, "html.parser").find("html")
    return found if isinstance(found, Tag) else None


def is_flare(page: str) -> bool:
    """True when the page's own ``<html>`` element carries Flare's runtime attributes."""
    tag = _html_tag(page)
    return tag is not None and any(tag.has_attr(attr) for attr in _RUNTIME_ATTRS)


def help_root(page_url: str, page: str) -> str:
    """The help system's root directory: a topic names its way up, an entry shell sits in it."""
    parts = urlparse(page_url)
    last = parts.path.rsplit("/", 1)[-1]
    if not parts.path.endswith("/") and not names_a_file(last):
        page_url = parts._replace(path=f"{parts.path}/").geturl()
    tag = _html_tag(page)
    up = tag.get(_PATH_TO_ROOT) if tag is not None else None
    return urljoin(page_url, up if isinstance(up, str) and up else "./")


def _is_topic(page: str) -> bool:
    tag = _html_tag(page)
    runtime = tag.get(_RUNTIME) if tag is not None else None
    return isinstance(runtime, str) and runtime.lower().startswith("topic")


def _fetch(url: str, why: str) -> str:
    try:
        return http.fetch_text(url)[1]
    except Exception as exc:
        raise InvalidInputError(f"{url} is not fetchable — {why}") from exc


def _help_system(url: str) -> tuple[str, str | None]:
    """The TOC file and output file ``Data/HelpSystem.xml`` names."""
    text = _fetch(url, "a Flare help system names its TOC there.")
    try:
        system = ET.fromstring(text.lstrip("\ufeff"))
    except ET.ParseError as exc:
        raise InvalidInputError(f"{url} is not a Flare help system file") from exc
    if system.tag != "WebHelpSystem":
        raise InvalidInputError(f"{url} is not a Flare help system file")
    toc = system.get("Toc")
    if not toc:
        raise InvalidInputError(f"{url} names no TOC (WebHelpSystem Toc=)")
    return toc, system.get("OutputFile")


def _entries(toc_url: str) -> list[TocEntry]:
    http.polite_sleep()
    toc = read_toc(_fetch(toc_url, "the help system's TOC."), source=toc_url)
    placements: dict[tuple[int, int], tuple[str, str]] = {}
    for chunk, name in enumerate(toc.chunk_files):
        chunk_url = urljoin(toc_url, name)
        http.polite_sleep()
        placements.update(
            read_chunk(_fetch(chunk_url, "part of the TOC."), chunk, source=chunk_url)
        )
    entries = toc_entries(toc, placements)
    if not any(entry.path is not None for entry in entries):
        raise InvalidInputError(f"{toc_url} lists no topic pages")
    return entries


def _name(output: str | None, toc_url: str, root: str) -> str | None:
    """The first of the output file, TOC and root directory names that is not a default."""
    toc_stem = toc_url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    candidates = [_PAGE_SUFFIX_RE.sub("", output.rsplit("/", 1)[-1])] if output else []
    candidates.append(_TOC_SUFFIX_RE.sub("", toc_stem))
    candidates.extend(reversed([seg for seg in urlparse(root).path.split("/") if seg]))
    for name in candidates:
        if name and name.lower() not in _GENERIC_NAMES and not _LOCALE_RE.match(name):
            return name
    return None


def _identity(slug: str, title: str | None, name: str | None) -> tuple[str, str | None]:
    """One host serves many manuals, so the slug carries the manual's name too."""
    if name is None:
        return slug, title
    named = slugify(name)
    if named != slug and not named.startswith(f"{slug}-"):
        named = slugify(f"{slug}-{name}") or slug
    words = [word for word in re.split(r"[\s_-]+", name) if word]
    # A lowercase directory name reads as a title once capitalized; an author's casing stays.
    spaced = " ".join(word.capitalize() if name.islower() else word for word in words)
    return named, title or spaced


def _page_url(root: str, path: str) -> str:
    return urljoin(root, quote(path, safe="/%"))


def acquire(base: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    seed_url, seed = http.fetch_text(base)
    root = help_root(seed_url, seed)
    if _is_topic(seed):
        title = None  # a topic's title names the topic, not the manual
    http.polite_sleep()
    toc_rel, output = _help_system(f"{root}{_HELP_SYSTEM}")
    toc_url = urljoin(root, toc_rel)
    entries = _entries(toc_url)
    slug, title = _identity(slug, title, _name(output, toc_url, root))
    topics = list(dict.fromkeys(_page_url(root, e.path) for e in entries if e.path is not None))
    truncated = len(topics) > _MAX_PAGES
    if truncated:
        log.warning("flare.capped", found=len(topics), cap=_MAX_PAGES)

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    def fetch(entry: TocEntry) -> Section:
        url = _page_url(root, entry.path or "")
        try:
            final, body = http.fetch_text(url)
        except Exception as exc:
            raise TopicLost(f"{url}: {exc}") from exc
        node = extract(body, final) if final.startswith(root) else None
        if node is None:
            raise TopicLost(f"no topic body at {final} (from {url})")
        digest = content_hash(node.decode_contents())
        seat_headings(node, depth=entry.depth, name=entry.title)
        stem = raw_stem(_PAGE_SUFFIX_RE.sub("", entry.path or ""))
        return Section(node.decode_contents(), digest, final, stem)

    staged = stage_toc(
        entries,
        raw_dir,
        key=lambda entry: _page_url(root, entry.path) if entry.path is not None else None,
        fetch=fetch,
        max_pages=_MAX_PAGES,
        event="flare",
    )

    log.info(
        "flare.acquire", root=root, toc=toc_url, found=len(topics), pages=staged.pages, slug=slug
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
