"""SCHEMA ST4 (Quanos) acquisition for docs_probe — TOC-driven, no crawl.

ST4 output has two faces, the split that also makes Paligo unrecognizable from
its landing page: a **topic** page announces the generator, the **entry** page
advertises only the publisher's stylesheet. Detection is therefore tell-based.

The page index is ``<base>/js/treedata.json`` — named .json and served as
application/json, but a JavaScript source file, not JSON.

Only the tree's **leaves** hold content; branch pages are client-rendered
shells, so their titles exist nowhere but the tree and are synthesized into the
fragment that opens each chapter.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger
from pf_core.utils.slugify import slugify

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns._site import (
    absolutize_refs,
    flatten_responsive_images,
    names_a_file,
    strip_scripts,
)

log = get_logger(__name__)

_MAX_PAGES = 5000
_CONTENT_CSS = 'div.container[role="main"]'
_ENTRY_TELLS = ("schema.de/2010/ST4", 'src="js/treedata.json"', "YMH_HTML_Manual")
_GENERATOR_RE = re.compile(r'name="generator"[^>]*content="([^"]*)"', re.I)
_PROJECT_TITLE_RE = re.compile(r'projectTitle\s*=\s*"([^"]*)"')
_TITLE_TAG_RE = re.compile(r"^\[[^\]]*\]")
_MAX_HEADING = 6

# {"text": str, "id": str, "href": str, "reused": bool, "nodes": [...]} — "nodes"
# present means a branch, whose page is an empty client-rendered shell.
TocNode = dict[str, Any]


def is_st4(html: str) -> bool:
    """True for an ST4 topic (generator meta) or its entry shell (tells)."""
    gen = _GENERATOR_RE.search(html)
    if gen and "st4" in gen.group(1).lower():
        return True
    return sum(tell in html for tell in _ENTRY_TELLS) >= 2


def publication_base(url: str) -> str:
    """The directory holding the topics and ``js/treedata.json``.

    Accepts both the entry file and its bare directory — a trailing segment is
    only stripped when it names a file.
    """
    trimmed = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    head, _, last = trimmed.rpartition("/")
    return head if names_a_file(last) else trimmed


def _json_array_at(text: str, start: int) -> str:
    """Slice the bracket-balanced array beginning at ``start``.

    A regex to the next assignment would cut early on a ``]`` inside a title,
    and the file appends six more assignments after the array.
    """
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise InvalidInputError("treedata.json holds an unterminated tocData array")


def parse_treedata(raw: str) -> list[TocNode]:
    """The tocData tree. ``raw`` is JS source, and may carry a UTF-8 BOM."""
    text = raw.lstrip("﻿")
    m = re.search(r"tocData\s*=\s*", text)
    if not m:
        raise InvalidInputError(
            "treedata.json has no tocData assignment — despite the name it is a "
            "JavaScript source file, and this one is not the ST4 shape."
        )
    bracket = text.find("[", m.end())
    if bracket == -1:
        raise InvalidInputError("treedata.json's tocData is not an array")
    try:
        tree = json.loads(_json_array_at(text, bracket))
    except ValueError as exc:
        raise InvalidInputError(f"treedata.json's tocData is not parseable: {exc}") from exc
    if not isinstance(tree, list):
        raise InvalidInputError("treedata.json's tocData is not an array")
    return tree


def project_title(raw: str) -> str | None:
    """The manual's own name, with any leading output-format tag stripped."""
    m = _PROJECT_TITLE_RE.search(raw.lstrip("﻿"))
    if not m:
        return None
    return _TITLE_TAG_RE.sub("", m.group(1)).strip() or None


def _walk(nodes: list[TocNode], depth: int = 0) -> list[tuple[int, TocNode, bool]]:
    """Flatten depth-first to (depth, node, is_branch)."""
    out: list[tuple[int, TocNode, bool]] = []
    for node in nodes:
        children = node.get("nodes")
        if isinstance(children, list) and children:
            out.append((depth, node, True))
            out.extend(_walk(children, depth + 1))
        else:
            out.append((depth, node, False))
    return out


def _strip_query(root: Tag) -> None:
    """Drop ``?page=`` viewer routes from topic cross-links.

    The query expands a TOC branch and shows a descendant; the same topic is
    also linked bare, so leaving it makes one page two targets.
    """
    for tag in root.find_all("a"):
        href = tag.get("href")
        if isinstance(href, str) and href:
            parts = urlparse(href)
            if parts.query and parts.path.lower().endswith(".html"):
                tag["href"] = urlunparse(parts._replace(query=""))


def _extract(page_html: str, page_url: str, depth: int) -> str | None:
    soup = BeautifulSoup(page_html, "html.parser")
    node = soup.select_one(_CONTENT_CSS)
    if not isinstance(node, Tag):
        return None
    for crumb in node.select("ol.breadcrumb"):
        crumb.decompose()
    strip_scripts(node)
    # The topic's own h1 would outrank its synthesized chapter heading,
    # inverting the hierarchy the tree encodes.
    for h1 in node.find_all("h1"):
        h1.name = f"h{min(depth + 1, _MAX_HEADING)}"
    flatten_responsive_images(node)
    absolutize_refs(node, page_url)
    _strip_query(node)
    return str(node)


def _heading(depth: int, text: str) -> str:
    level = min(depth + 1, _MAX_HEADING)
    return f"<h{level}>{html.escape(text)}</h{level}>"


def acquire(url: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    base = publication_base(url)
    index_url = f"{base}/js/treedata.json"
    try:
        _f, raw = http.fetch_text(index_url)
    except Exception as exc:
        raise InvalidInputError(
            f"{index_url} is not fetchable — an ST4 manual keeps its whole topic "
            "list there; without it the topics cannot be enumerated."
        ) from exc

    flat = _walk(parse_treedata(raw))
    leaves = [(d, n) for d, n, is_branch in flat if not is_branch]
    # The host label alone collides across every model a publisher ships; the
    # manual names itself in the same file that lists its topics.
    project = project_title(raw)
    if project:
        slug = slugify(f"{slug}-{project}") or slug
        title = title or project
    truncated = len(leaves) > _MAX_PAGES
    if truncated:
        log.warning("st4.capped", found=len(leaves), cap=_MAX_PAGES)

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    pending: list[str] = []
    saved = 0
    lost = 0
    for depth, node, is_branch in flat:
        if is_branch:
            pending.append(_heading(depth, node.get("text", "")))
            continue
        if saved >= _MAX_PAGES:
            break
        href = node.get("href") or f"{node.get('id', '')}.html"
        page_url = f"{base}/{href}"
        try:
            final, body = http.fetch_text(page_url)
        except Exception as exc:
            lost += 1
            log.warning("st4.fetch_error", url=page_url, error=str(exc))
            http.polite_sleep()
            continue
        fragment = _extract(body, final, depth)
        if fragment is None:
            lost += 1
            log.warning("st4.no_content", url=page_url)
            http.polite_sleep()
            continue
        # href is remote-controlled (treedata.json); flatten as the siblings do so
        # a nested path can't name a directory that was never created.
        stem = href.split("?", 1)[0].strip("/").replace("/", "-").removesuffix(".html") or "topic"
        (raw_dir / f"{saved:04d}-{stem}.html").write_text(
            f"<!-- source: {page_url} -->\n<section>\n"
            + "".join(pending)
            + f"\n{fragment}\n</section>\n",
            encoding="utf-8",
        )
        pending.clear()
        saved += 1
        http.polite_sleep()

    log.info("st4.acquire", base=base, found=len(leaves), pages=saved, slug=slug)
    return AcquireResult(
        raw_dir=raw_dir,
        kind="html",
        slug=slug,
        pages=saved,
        title=title,
        truncated=truncated,
        lost=lost,
    )
