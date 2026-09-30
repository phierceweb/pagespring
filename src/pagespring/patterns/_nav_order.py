"""Staging order from a site's sidebar: one spelling per page, and a place for every
page the sidebar omits."""

from __future__ import annotations

from urllib.parse import urldefrag, urlparse


def page_key(url: str) -> str:
    """One spelling per page: no fragment, trailing ``index.html`` or trailing slash."""
    bare = urldefrag(url)[0]
    if bare.endswith("/index.html"):
        bare = bare[: -len("index.html")]
    return bare.rstrip("/")


def _lineage(key: str) -> list[str]:
    """``key`` and every directory above it up to the origin, nearest first."""
    p = urlparse(key)
    segs = [s for s in p.path.split("/") if s]
    origin = f"{p.scheme}://{p.netloc}"
    return ["/".join([origin, *segs[:n]]) for n in range(len(segs), -1, -1)]


def reading_order(pages: list[str], sidebar: list[str]) -> list[str]:
    """``pages`` in ``sidebar`` order; with no sidebar the given order stands.

    A page the sidebar omits goes just before its first listed descendant (a
    section page), else just after the listed pages under its nearest ancestor,
    nearer ancestors first, then in tree order."""
    if not sidebar:
        return pages
    listed: dict[str, int] = {}
    first: dict[str, int] = {}
    last: dict[str, int] = {}
    for i, key in enumerate(page_key(u) for u in sidebar):
        listed.setdefault(key, i)
        for anc in _lineage(key):
            first.setdefault(anc, i)
            last[anc] = i

    def rank(url: str) -> tuple[int, int, int, tuple[str, ...]]:
        key = page_key(url)
        if key in listed:
            return listed[key], 0, 0, ()
        tree = tuple(s for s in urlparse(key).path.split("/") if s)
        if key in first:
            return first[key], -1, 0, tree
        above = next((a for a in _lineage(key)[1:] if a in last), None)
        if above is None:
            return len(sidebar), 1, 0, tree
        return last[above], 1, -above.count("/"), tree

    return sorted(pages, key=rank)
