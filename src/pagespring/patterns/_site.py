"""Shared helpers for generator-built docs sites (used by docs_probe and its
strategy modules): host→slug, <title>, <meta generator>, path-segment tests, and
in-place fragment surgery — absolutizing refs and flattening responsive images."""

from __future__ import annotations

import re
from contextlib import suppress
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag

_GENERIC_LABELS = {"www", "docs", "manual", "manuals", "help", "support"}
# A file extension, not merely any dot — "v2.1" and "example.test" are not files.
_FILE_SUFFIX_RE = re.compile(r"\.[A-Za-z][A-Za-z0-9]{1,5}$")


def names_a_file(segment: str) -> bool:
    """True when a path segment looks like a filename rather than a directory.

    Crawl patterns strip a trailing filename to get the base directory; stripping
    a directory instead scopes the crawl one level too high."""
    return bool(_FILE_SUFFIX_RE.search(segment))


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


def strip_scripts(root: Tag) -> None:
    """Drop <script>/<style>/<noscript> from an extracted fragment."""
    for junk in root.find_all(["script", "style", "noscript"]):
        junk.decompose()


_CARRIERS = (
    "data-src",
    "data-lazy-src",
    "data-original",
    "srcset",
    "data-srcset",
    "sizes",
    "originalimagename",  # publisher build metadata naming a file that never ships
)
# A media query that can never match — the variant behind it is never rendered.
# Publishers write it both bare and parenthesized; Apple ships "(not all)".
_DEAD_MEDIA_RE = re.compile(r"^\s*\(?\s*not\s+all\s*\)?\s*$", re.I)
# Declared pixel width: a srcset "w" descriptor, or a CDN sizing parameter.
_WIDTH_PARAM_RE = re.compile(r"[?&](?:wid|width|w)=(\d+)", re.I)
_LOCAL_WINS = 1 << 30


def _is_real_ref(value: object) -> bool:
    """A usable image reference — not empty, not an inline placeholder."""
    return isinstance(value, str) and bool(value.strip()) and not value.startswith("data:")


def _srcset_candidates(srcset: str) -> list[tuple[str, int, float]]:
    """(url, declared width, pixel density) per srcset candidate.

    Rejects a ``data:`` value whole: its own comma would split into base64
    fragments that look like relative URLs."""
    if not _is_real_ref(srcset):
        return []
    out: list[tuple[str, int, float]] = []
    for candidate in srcset.split(","):
        parts = candidate.split()
        if not parts or not _is_real_ref(parts[0]):
            continue
        width, density = 0, 0.0
        for token in parts[1:]:
            if token.endswith("w") and token[:-1].isdigit():
                width = int(token[:-1])
            elif token.endswith("x"):
                with suppress(ValueError):
                    density = float(token[:-1])
        out.append((parts[0], width or _declared_width(parts[0]), density))
    return out


def _declared_width(url: str) -> int:
    """Pixel width the URL itself asks for (Scene7 ``wid=``, ``width=``), else 0."""
    m = _WIDTH_PARAM_RE.search(url)
    return int(m.group(1)) if m else 0


def _candidates(img: Tag, sources: list[Tag]) -> list[tuple[int, float, int, str]]:
    """(width, density, -precedence, url) for every rendition of one image.

    A source behind a media query that can never match is excluded — the page
    never renders it."""
    out: list[tuple[int, float, int, str]] = []
    local = img.get("src")
    if isinstance(local, str) and local.startswith("images/"):
        return [(_LOCAL_WINS, 0.0, 0, local)]  # idempotent over a localized doc
    for rank, attr in enumerate(("src", "data-src", "data-lazy-src", "data-original")):
        val = img.get(attr)
        if _is_real_ref(val) and isinstance(val, str):
            out.append((_declared_width(val), 0.0, -rank, val))
    for rank, attr in enumerate(("srcset", "data-srcset"), start=4):
        val = img.get(attr)
        if isinstance(val, str):
            out.extend((w, d, -rank, u) for u, w, d in _srcset_candidates(val))
    for source in sources:
        media = source.get("media")
        if isinstance(media, str) and _DEAD_MEDIA_RE.match(media):
            continue
        for rank, attr in enumerate(("srcset", "data-srcset"), start=6):
            val = source.get(attr)
            if isinstance(val, str):
                out.extend((w, d, -rank, u) for u, w, d in _srcset_candidates(val))
    return out


def _best_ref(img: Tag, sources: list[Tag] | None = None) -> str | None:
    """The image's one true URL: widest declared rendition, then precedence.

    With no width declared anywhere this falls back to ``src``, the publisher's
    canonical asset."""
    cands = _candidates(img, sources or [])
    if not cands:
        return None
    return max(cands)[3]


def flatten_responsive_images(root: Tag) -> None:
    """Reduce every responsive image to one plain ``<img src>``, widest first.

    The localizer follows ``<img src>`` and markdown ``](…)`` only, so anything
    parked in ``<picture>``/``srcset``/``data-src`` ships remote whatever
    ``localize`` does."""

    def settle(img: Tag, sources: list[Tag]) -> bool:
        """Resolve and apply this image's one URL; False when there is none."""
        winner = _best_ref(img, sources)
        if winner is None:
            return False
        img["src"] = winner
        for attr in _CARRIERS:
            if img.get(attr) is not None:
                del img[attr]
        return True

    for picture in root.find_all("picture"):
        sources = [s for s in picture.find_all("source") if isinstance(s, Tag)]
        img = picture.find("img")
        if not isinstance(img, Tag):
            made = BeautifulSoup('<img alt=""/>', "html.parser").img
            if made is None:
                picture.decompose()
                continue
            img = made
        if settle(img, sources):
            picture.replace_with(img)
        else:
            picture.decompose()

    for img in root.find_all("img"):
        if not settle(img, []):
            img.decompose()  # an empty lazy-load shell — nothing to recover
