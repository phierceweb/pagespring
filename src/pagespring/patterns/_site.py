"""Shared helpers for generator-built docs sites (used by docs_probe and its
strategy modules): host→slug, <title>, <meta generator>, and in-place
absolutization of a content fragment's refs."""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag

_GENERIC_LABELS = {"www", "docs", "manual", "manuals", "help", "support"}


def slug_from_host(host: str) -> str:
    """Short id from a hostname: drop a generic leading label, take the next."""
    labels = [label for label in host.lower().split(":")[0].split(".") if label]
    if len(labels) > 1 and labels[0] in _GENERIC_LABELS:
        labels = labels[1:]
    return labels[0].replace("_", "-") if labels else "docs"


def page_title(html: str) -> str | None:
    """The page's <title> text, or None."""
    soup = BeautifulSoup(html, "html.parser")
    return soup.title.get_text(strip=True) if soup.title else None


def generator_meta(html: str) -> str:
    """Lowercased <meta name="generator"> content ('' when absent)."""
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("meta", attrs={"name": "generator"})
    if isinstance(tag, Tag):
        return str(tag.get("content") or "").lower()
    return ""


def absolutize_refs(root: Tag, page_url: str) -> None:
    """Make every <a href> / <img src> under root absolute against page_url.

    Fragment-only, mailto:, data:, and already-absolute refs are left alone."""
    for tag in root.find_all(["a", "img"]):
        for attr in ("href", "src"):
            val = tag.get(attr)
            if (
                isinstance(val, str)
                and val
                and not val.startswith(("http://", "https://", "#", "mailto:", "data:"))
            ):
                tag[attr] = urljoin(page_url, val)
