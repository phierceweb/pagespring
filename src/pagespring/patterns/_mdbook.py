"""mdBook acquisition for docs_probe — the print page, else a TOC-ordered crawl.

mdBook renders the whole book into ``print.html`` at the book root: every chapter
in SUMMARY order, split by page-break divs, so one fetch replaces a crawl. A book
can switch the print page off; the sidebar TOC then drives a crawl in reading order.

The root comes from the page's own ``path_to_root``, so a chapter seed scopes to
its book. Every chapter renders its title as ``<h1>`` whatever its nesting, so each
chapter's headings shift by its TOC depth to sit under its parent.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from bs4.element import Comment, Tag
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger
from pf_core.utils.hashing import content_hash
from pf_core.utils.slugify import slugify

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.config import cfg
from pagespring.liveness import ProgressWatchdog
from pagespring.patterns._site import (
    absolutize_refs,
    flatten_responsive_images,
    names_a_file,
    page_title,
    raw_stem,
    strip_scripts,
)

log = get_logger(__name__)

_MAX_PAGES = 2000
_GENERATOR_RE = re.compile(r"<!--\s*Book generated using mdBook\b")
_PATH_TO_ROOT_RE = re.compile(r"""\bpath_to_root\s*=\s*["']([^"']*)["']""")
# The default theme keeps its pager outside <main>; custom themes put a pager and
# the site footer inside it.
_CHROME_CSS = "nav, footer, div.footer-buttons"
_HEADING_RE = re.compile(r"^h[1-6]$")
_NUMBERING_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s+")


@dataclass(frozen=True)
class _Chapter:
    url: str
    depth: int
    name: str


def is_mdbook(html: str) -> bool:
    """True when the page carries the comment mdBook's page template emits."""
    return bool(_GENERATOR_RE.search(html))


def book_root(page_url: str, html: str) -> str:
    """The directory holding the book's ``print.html`` and ``toc.html``."""
    m = _PATH_TO_ROOT_RE.search(html)
    rel: str = m.group(1) if m else ""
    return urljoin(page_url, rel or "./")


def _words(tag: Tag) -> list[str]:
    return re.sub(r"\W+", " ", tag.get_text(" ")).lower().split()


def _repeats(first: Tag, second: Tag) -> bool:
    """``first`` is an ``<h1>`` whose words recur, in order, in the deeper heading
    right after it, with no text between."""
    if first.name != "h1" or second.name == "h1" or not _words(first):
        return False
    for sibling in first.next_siblings:
        if sibling is second:
            rest = iter(_words(second))
            return all(word in rest for word in _words(first))
        if isinstance(sibling, Comment):
            continue
        text = sibling.get_text(strip=True) if isinstance(sibling, Tag) else str(sibling).strip()
        if text:
            return False
    return False


def fix_headings(node: Tag, *, depth: int, name: str) -> None:
    """Seat a chapter at its TOC depth: its first heading becomes ``h{depth + 1}``,
    the rest move with it, none rising above it or past ``h6``. A chapter with no
    heading gets its TOC name. The ``<h1>`` the print page adds over a chapter that
    opens lower goes where the chapter's own heading repeats it."""
    top = min(depth + 1, 6)
    heads = node.find_all(_HEADING_RE)
    if len(heads) > 1 and _repeats(heads[0], heads[1]):
        heads.pop(0).decompose()
    if not heads:
        if name:
            node.insert(0, BeautifulSoup(f"<h{top}>{escape(name)}</h{top}>", "html.parser"))
        return
    shift = top - int(heads[0].name[1])
    for head in heads:
        head.name = f"h{min(max(int(head.name[1]) + shift, top), 6)}"


def _seed(url: str) -> str:
    """A directory seed with its trailing slash back: docs_probe strips it, and it
    is the base every relative ref on the entry page resolves against."""
    p = urlparse(url.split("#", 1)[0])
    if p.path.endswith("/") or names_a_file(p.path.rsplit("/", 1)[-1]):
        return urlunparse(p)
    return urlunparse(p._replace(path=p.path + "/"))


def _toc(soup: BeautifulSoup, base_url: str, root: str) -> list[_Chapter]:
    """Linked chapters in reading order with their nesting depth.

    Depth counts enclosing lists, not items: the TOC leaves a parent ``<li>``
    unclosed, and older themes put a section list in a sibling ``<li>``."""
    top = soup.select_one("ol.chapter")
    if top is None:
        return []
    chapters: list[_Chapter] = []
    for a in top.find_all("a", href=True):
        href = a["href"]
        if not isinstance(href, str) or href.startswith("#"):
            continue
        url = urljoin(base_url, href).split("#", 1)[0]
        if not url.startswith(root):
            continue
        depth = 0
        for parent in a.parents:
            if parent is top:
                break
            depth += parent.name == "ol"
        name = _NUMBERING_RE.sub("", a.get_text(" ", strip=True))
        chapters.append(_Chapter(url, depth, name))
    return chapters


def _optional(url: str) -> tuple[str, str] | None:
    try:
        return http.fetch_text(url)
    except Exception as exc:
        log.info("mdbook.unavailable", url=url, error=str(exc))
        return None


def _clean(node: Tag, page_url: str) -> None:
    for el in node.select(_CHROME_CSS):
        el.decompose()
    strip_scripts(node)
    flatten_responsive_images(node)
    absolutize_refs(node, page_url)


def _print_chapters(html: str) -> list[Tag] | None:
    """The print page's ``<main>`` cut at its page-break divs, one node per chapter."""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main")
    if not isinstance(main, Tag):
        return None
    parts = [soup.new_tag("div")]
    for child in list(main.children):
        style = child.get("style") if isinstance(child, Tag) else None
        if (
            isinstance(child, Tag)
            and child.name == "div"
            and "break-before" in str(style or "")
            and not child.get_text(strip=True)
        ):
            parts.append(soup.new_tag("div"))
        else:
            parts[-1].append(child.extract())
    return parts


def _stage(raw_dir: Path, index: int, url: str, node: Tag, root: str) -> None:
    stem = raw_stem(url[len(root) :].removesuffix(".html"))
    (raw_dir / f"{index:04d}-{stem}.html").write_text(
        f"<!-- source: {url} -->\n<section>\n{node.decode_contents()}\n</section>\n",
        encoding="utf-8",
    )


def _stage_print(
    parts: list[Tag], chapters: list[_Chapter], print_url: str, *, root: str, raw_dir: Path
) -> None:
    if not chapters:
        log.warning("mdbook.no_toc", root=root)
    for i, part in enumerate(parts):
        chapter = chapters[i] if chapters else _Chapter(print_url, 0, "")
        _clean(part, print_url)
        fix_headings(part, depth=chapter.depth, name=chapter.name)
        _stage(raw_dir, i, chapter.url, part, root)


def _crawl(
    chapters: list[_Chapter], *, root: str, raw_dir: Path, bodies: dict[str, str]
) -> tuple[int, int, bool]:
    """Stage each chapter page's ``<main>`` in TOC order: (saved, lost, truncated)."""
    truncated = len(chapters) > _MAX_PAGES
    if truncated:
        log.warning("mdbook.capped", found=len(chapters), cap=_MAX_PAGES)
    todo = chapters[:_MAX_PAGES]
    saved = lost = 0
    staged: set[str] = set()  # final URLs and content hashes
    watchdog = ProgressWatchdog(stall_after_s=cfg.CRAWL_STALL_AFTER_S, now=time.monotonic)
    for chapter in todo:
        if watchdog.stalled():
            log.warning("mdbook.stalled", saved=saved, idle_s=round(watchdog.idle_s()))
            truncated = True
            break
        url, body = chapter.url, bodies.pop(chapter.url, None)
        if body is None:
            http.polite_sleep()
            try:
                url, body = http.fetch_text(chapter.url)
            except Exception as exc:
                lost += 1
                log.warning("mdbook.fetch_error", url=chapter.url, error=str(exc))
                continue
        if not url.startswith(root):
            lost += 1
            log.warning("mdbook.left_book", url=url, root=root)
            continue
        main = BeautifulSoup(body, "html.parser").find("main")
        if not isinstance(main, Tag):
            lost += 1
            log.warning("mdbook.no_main", url=url)
            continue
        _clean(main, url)
        # A redirect alias and its target are one page under two names.
        digest = content_hash(main.decode_contents())
        if url in staged or digest in staged:
            continue
        staged |= {url, digest}
        fix_headings(main, depth=chapter.depth, name=chapter.name)
        _stage(raw_dir, saved, url, main, root)
        saved += 1
        watchdog.progress()
    return saved, lost, truncated


def acquire(url: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    entry_url, entry = http.fetch_text(_seed(url))
    root = book_root(entry_url, entry)
    soup = BeautifulSoup(entry, "html.parser")
    chapters = _toc(soup, entry_url, root)
    if not chapters:
        http.polite_sleep()
        toc = _optional(f"{root}toc.html")
        if toc is not None:
            chapters = _toc(BeautifulSoup(toc[1], "html.parser"), toc[0], root)
    http.polite_sleep()
    printed = _optional(f"{root}print.html")
    print_page = printed if printed is not None and is_mdbook(printed[1]) else None

    menu = soup.select_one(".menu-title")
    book_title = (menu.get_text(" ", strip=True) if menu else "") or (
        page_title(print_page[1]) if print_page else None
    )
    if book_title:
        slug = slugify(f"{slug}-{book_title}") or slug

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    parts = _print_chapters(print_page[1]) if print_page else None
    # Depth is assigned by position, so a print page is only trusted chapter for chapter.
    if print_page and parts is not None and (not chapters or len(parts) == len(chapters)):
        _stage_print(parts, chapters, print_page[0], root=root, raw_dir=raw_dir)
        saved, lost, truncated, source = len(parts), 0, False, "print"
    elif chapters:
        if parts is not None:
            log.warning("mdbook.print_toc_mismatch", print=len(parts), toc=len(chapters))
        bodies = {entry_url: entry}
        saved, lost, truncated = _crawl(chapters, root=root, raw_dir=raw_dir, bodies=bodies)
        source = "crawl"
    else:
        raise InvalidInputError(
            f"{entry_url} is an mdBook page, but {root} serves neither print.html nor a "
            "sidebar TOC (toc.html) to read the chapters from."
        )

    log.info("mdbook.acquire", root=root, source=source, pages=saved, lost=lost, slug=slug)
    return AcquireResult(
        raw_dir=raw_dir,
        kind="html",
        slug=slug,
        pages=saved,
        title=book_title or title,
        truncated=truncated,
        lost=lost,
    )
