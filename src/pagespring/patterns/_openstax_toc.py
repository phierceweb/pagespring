"""OpenStax's book tree, embedded in every REX page as ``window.__PRELOADED_STATE__``, read into the
top-level nodes that each open a chapter or back-matter heading in the deliverable."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bs4 import Tag
from pw_foundation.log import get_logger

from pagespring.patterns._soup import parse_html

log = get_logger(__name__)

TREE_NAME = "book-tree.json"
_STATE_RE = re.compile(r"window\.__PRELOADED_STATE__\s*=\s*")
_CHAPTER_RE = re.compile(r"^(\d+)-")
_SOURCE_RE = re.compile(r"<!-- source: (\S+) -->")
# Every node type OpenStax trees use; any other makes the tree unreadable rather than guessed at.
_CONTAINERS = frozenset({"unit", "chapter", "sub-book-tree"})
_PAGE = "book-content"
# Node levels far deeper than any book's tree. Each level is a dict and a list, so a
# tree whose JSON nests past twice this is refused before the walks or the JSON writer recurse.
_MAX_NESTING = 200
# A lone surrogate decodes from a JSON escape, but no UTF-8 write can encode it.
_SURROGATE_RE = re.compile("[\ud800-\udfff]")


@dataclass(frozen=True)
class TocNode:
    """A top-level node of the book tree once units are flattened, with every page slug under it."""

    toc_type: str
    toc_target_type: str | None
    title: str
    number: str | None
    slugs: tuple[str, ...]


def book_tree(html: str) -> dict[str, Any] | None:
    """The ``content.book.tree`` object of a REX page's embedded state, or None when absent."""
    m = _STATE_RE.search(html)
    if m is None:
        return None
    try:
        state, _end = json.JSONDecoder().raw_decode(html, m.end())
    except (ValueError, RecursionError):
        return None
    content = state.get("content") if isinstance(state, dict) else None
    book = content.get("book") if isinstance(content, dict) else None
    tree = book.get("tree") if isinstance(book, dict) else None
    if isinstance(tree, dict) and isinstance(tree.get("contents"), list) and not _unwritable(tree):
        return tree
    return None


def _unwritable(tree: dict[str, Any]) -> bool:
    """Whether ``tree`` nests past the cap or holds a lone surrogate in any key or string."""
    stack: list[tuple[dict[str, Any] | list[Any], int]] = [(tree, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > 2 * _MAX_NESTING:
            return True
        for item in [*value, *value.values()] if isinstance(value, dict) else value:
            if isinstance(item, (dict, list)):
                stack.append((item, depth + 1))
            elif isinstance(item, str) and _SURROGATE_RE.search(item):
                return True
    return False


def chapter_number(slug: str) -> int | None:
    """The chapter a page slug names by its leading integer (``9-6-forces…`` → 9)."""
    m = _CHAPTER_RE.match(slug)
    return int(m.group(1)) if m else None


def part_slug(part: str) -> str | None:
    """The page slug of a raw part, from the ``<!-- source: … -->`` line it starts with."""
    m = _SOURCE_RE.match(part)
    if m is None:
        return None
    path = urlparse(m.group(1)).path
    if "/pages/" not in path:
        return None
    return path.rsplit("/pages/", 1)[1].strip("/") or None


def _title(raw: object) -> tuple[str, str | None] | None:
    if not isinstance(raw, str):
        return None
    soup = parse_html(raw)
    num = soup.find(class_="os-number")
    number = num.get_text(strip=True) if isinstance(num, Tag) else None
    return " ".join(soup.get_text().split()), number


def _pages(node: object, depth: int = 0) -> list[str] | None:
    """Every page slug under ``node`` in reading order; None on a node of unknown shape."""
    if not isinstance(node, dict) or depth > _MAX_NESTING:
        return None
    kind = node.get("toc_type")
    if kind == _PAGE:
        slug = node.get("slug")
        return [slug] if isinstance(slug, str) and slug else None
    children = node.get("contents")
    if kind not in _CONTAINERS or not isinstance(children, list):
        return None
    out: list[str] = []
    for child in children:
        found = _pages(child, depth + 1)
        if found is None:
            return None
        out.extend(found)
    return out


def toc_nodes(tree: dict[str, Any], *, _depth: int = 0) -> list[TocNode] | None:
    """The tree's top-level nodes with units flattened into their chapters; None on an unknown node."""
    nodes: list[TocNode] = []
    contents = tree.get("contents")
    if not isinstance(contents, list) or _depth > _MAX_NESTING:
        return None
    for node in contents:
        if isinstance(node, dict) and node.get("toc_type") == "unit":
            inner = toc_nodes(node, _depth=_depth + 1)
            if inner is None:
                return None
            nodes.extend(inner)
            continue
        slugs = _pages(node)
        title = _title(node.get("title")) if isinstance(node, dict) else None
        if slugs is None or title is None or not title[0]:
            log.info("openstax.toc_unknown_node", node=str(node)[:200])
            return None
        target = node.get("toc_target_type")
        nodes.append(
            TocNode(
                toc_type=node["toc_type"],
                toc_target_type=target if isinstance(target, str) else None,
                title=title[0],
                number=title[1],
                slugs=tuple(slugs),
            )
        )
    return nodes


def _node_chapter(node: TocNode) -> int | None:
    if node.toc_type != "chapter":
        return None
    return int(node.number) if node.number and node.number.isdecimal() else -1


def heading_plan(slugs: list[str | None], nodes: list[TocNode]) -> list[tuple[int, str]] | None:
    """``(part index, title)`` for the first staged part of each node but a leading preface; None
    (logged) when the tree disagrees with the parts' slugs, whose chapter grouping it must match."""
    owner: dict[str, int] = {}
    for at, node in enumerate(nodes):
        for s in node.slugs:
            if owner.setdefault(s, at) != at:
                log.warning("openstax.tree_mismatch", reason="page_in_two_nodes", slug=s)
                return None
    plan: list[tuple[int, str]] = []
    last = -1
    for part, slug in enumerate(slugs):
        i = owner.get(slug) if slug else None
        if i is None:
            log.warning("openstax.tree_mismatch", reason="page_not_in_tree", slug=slug)
            return None
        if i < last:
            log.warning("openstax.tree_mismatch", reason="out_of_order", slug=slug)
            return None
        node = nodes[i]
        if _node_chapter(node) != chapter_number(slug or ""):
            log.warning(
                "openstax.tree_mismatch", reason="chapter_number", slug=slug, title=node.title
            )
            return None
        if i > last and (node.toc_target_type != "preface" or last >= 0):
            plan.append((part, node.title))
        last = i
    unstaged = {s for n in nodes for s in n.slugs} - {s for s in slugs if s}
    if unstaged:
        log.info("openstax.tree_pages_not_staged", count=len(unstaged), first=sorted(unstaged)[0])
    return plan


def raw_headings(raw_dir: Path, parts: list[str]) -> list[tuple[int, str]]:
    """The headings ``raw_dir``'s kept book tree places among ``parts``; none without a tree, or
    with one that is unreadable or disagrees with the parts."""
    path = raw_dir / TREE_NAME
    if not path.is_file():
        return []
    try:
        tree = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, RecursionError):
        tree = None
    nodes = toc_nodes(tree) if isinstance(tree, dict) and not _unwritable(tree) else None
    if nodes is None:
        log.warning("openstax.tree_mismatch", reason="unreadable_tree", path=str(path))
        return []
    return heading_plan([part_slug(p) for p in parts], nodes) or []
