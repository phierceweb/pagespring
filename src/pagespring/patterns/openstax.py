"""openstax — OpenStax textbooks (``openstax.org/books/<slug>/...``).

OpenStax's reader (REX) serves no full server-side table of contents, but every
page is a node in a Prev/Next linked list (``<a aria-label="Previous Page">`` /
``"Next Page"``). acquire seeds at the book's first page — walking Prev back from
the entry URL — then walks Next to the end, extracting each page's
``<main class="page-content">`` body. The page chrome (book banner, toolbar, the
Prev/Next bar itself) lives OUTSIDE ``<main>``, so extraction is clean; content
images are root-relative ``/apps/image-cdn/…`` URLs that get absolutized.
normalize concatenates the pages into ONE clean HTML file.

One pattern covers the whole catalogue — every OpenStax book shares this shape.
Point it at the book, e.g. ``https://openstax.org/books/microbiology`` (or any of
its ``/pages/<page>`` URLs).
"""

from __future__ import annotations

import html as _html
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag
from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult

log = get_logger(__name__)

_PARSER = "html.parser"
_MAX = 1500  # page cap — the largest OpenStax books run a few hundred pages
_TITLE_SUFFIX = " | OpenStax"


def _slug(url: str) -> str:
    """Book slug from ``…/books/<slug>[/pages/<page>]`` — the output dir name."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    if "books" in parts:
        i = parts.index("books")
        if i + 1 < len(parts):
            return parts[i + 1]
    return parts[-1] if parts else "openstax"


def _seed(url: str, origin: str) -> str:
    """Where to start the Prev-walk: the URL itself if it names a page, else the
    book's conventional first page (``preface``)."""
    if "/pages/" in urlparse(url).path:
        return url
    return f"{origin}/books/{_slug(url)}/pages/preface"


def _nav_href(soup: BeautifulSoup, label: str) -> str | None:
    """The href of the Prev/Next anchor identified by its stable ``aria-label``."""
    a = soup.find("a", attrs={"aria-label": label})
    if not isinstance(a, Tag):
        return None
    href = a.get("href")
    return href if isinstance(href, str) and href else None


def _book_title(soup: BeautifulSoup) -> str | None:
    """The book's human title from the page ``<title>`` ("<page> - <book> |
    OpenStax"), independent of the slug (which can drop words, e.g.
    ``concepts-biology`` for "Concepts of Biology")."""
    t = soup.find("title")
    if not isinstance(t, Tag):
        return None
    text = t.get_text(strip=True)
    if text.endswith(_TITLE_SUFFIX):
        text = text[: -len(_TITLE_SUFFIX)].strip()
    if " - " in text:
        text = text.rsplit(" - ", 1)[-1].strip()
    return text or None


def _extract(soup: BeautifulSoup, page_url: str) -> str | None:
    """One page's book content as a clean HTML fragment (absolute asset URLs),
    or None when the content container is absent (a non-content shell)."""
    main = soup.find("main", class_="page-content")
    if not isinstance(main, Tag):
        return None
    node = main.find(None, attrs={"data-type": "page"}) or main.find(id="main-content")
    if not isinstance(node, Tag):
        node = main
    for junk in node.find_all(["script", "style"]):
        junk.decompose()
    for img in node.find_all("img"):
        src = img.get("src")
        if isinstance(src, str) and src:
            img["src"] = urljoin(page_url, src)
    for a in node.find_all("a"):
        href = a.get("href")
        if isinstance(href, str) and href:
            a["href"] = urljoin(page_url, href)
    inner = node.decode_contents().strip()
    return inner or None


def _walk_to_first(seed: str) -> tuple[str, str]:
    """Follow Prev links from ``seed`` back to the book's first page; return its
    ``(url, html)``. Bounded by ``_MAX`` and cycle-guarded so a malformed chain
    can't loop forever."""
    url = seed
    seen: set[str] = set()
    _f, html = http.fetch_text(url)
    for _ in range(_MAX):
        seen.add(url)
        prev = _nav_href(BeautifulSoup(html, _PARSER), "Previous Page")
        if not prev:
            break
        nxt = urljoin(url, prev)
        if nxt in seen:
            break
        url = nxt
        http.polite_sleep()
        _f, html = http.fetch_text(url)
    return url, html


class OpenStaxPattern:
    name = "openstax"
    # Large multi-chapter books; pagespeak downloads the absolute image URLs.
    convert_recipe = ["--split-sections"]

    def match(self, url: str) -> bool:
        p = urlparse(url)
        return p.netloc.lower() == "openstax.org" and p.path.startswith("/books/")

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        slug = _slug(url)

        first_url, html = _walk_to_first(_seed(url, origin))

        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        cur: str | None = first_url
        visited: set[str] = set()
        saved = 0
        i = 0
        truncated = False
        book_title: str | None = None
        while cur is not None:
            if cur in visited:
                break  # cycle guard
            if i >= _MAX:
                truncated = True
                break
            visited.add(cur)
            if i > 0:  # the first page's html is already in hand from the Prev-walk
                try:
                    _f, html = http.fetch_text(cur)
                except Exception as exc:  # chain dead-ends without the next link
                    log.warning("openstax.fetch_error", url=cur, error=str(exc))
                    break
            soup = BeautifulSoup(html, _PARSER)
            if i == 0:
                book_title = _book_title(soup)
            content = _extract(soup, cur)
            if content:
                (raw_dir / f"{i:04d}.html").write_text(
                    f"<!-- source: {cur} -->\n<section>\n{content}\n</section>\n",
                    encoding="utf-8",
                )
                saved += 1
            i += 1
            nxt = _nav_href(soup, "Next Page")
            if nxt is None:
                break
            cur = urljoin(cur, nxt)
            http.polite_sleep()

        if truncated:
            log.warning("openstax.capped", saved=saved, cap=_MAX)
        log.info("openstax.acquire", url=url, slug=slug, pages=saved, title=book_title)
        return AcquireResult(raw_dir=raw_dir, kind="html", slug=slug, pages=saved, title=book_title)

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        title = acq.title or acq.slug.replace("-", " ").title()
        parts = [p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html"))]
        doc = (
            '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
            f"<title>{_html.escape(title)}</title></head>\n<body>\n"
            f"<h1>{_html.escape(title)}</h1>\n" + "\n".join(parts) + "\n</body></html>\n"
        )
        out = workdir / f"{acq.slug}.html"
        out.write_text(doc, encoding="utf-8")
        log.info("openstax.normalize", slug=acq.slug, out=str(out), pages=len(parts))
        return out
