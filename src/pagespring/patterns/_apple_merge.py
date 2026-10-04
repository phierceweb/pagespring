"""Merge saved Apple Support topic pages into one HTML manual: each ``#article-section`` body,
nested at its welcome-TOC depth, with the Apple.com chrome dropped and image URLs absolute."""

from __future__ import annotations

import html as _html
import re
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from pagespring.patterns._site import flatten_responsive_images, strip_scripts

_PARSER = "html.parser"
_SLUG_RE = re.compile(r"/guide/[^/]+/([^/]+)/")
_GUIDE_RE = re.compile(r"/guide/([^/]+)/")
_SKIP_SLUGS = {"welcome", "aside"}


def _slug_from(a: Tag) -> str | None:
    for attr in ("href", "data-ajax-endpoint"):
        val = a.get(attr)
        if isinstance(val, str):
            m = _SLUG_RE.search(val)
            if m:
                return m.group(1)
    return None


def toc_topic_slugs(welcome: Path, guide_slug: str) -> set[str]:
    """Every topic linked from welcome's TOC, saved or not, for the guide its canonical link names
    (``--slug`` renames the deliverable, not the guide)."""
    if not welcome.exists():
        return set()
    soup = BeautifulSoup(welcome.read_text(encoding="utf-8", errors="ignore"), _PARSER)
    container = soup.find(id="modal-toc-container")
    if not isinstance(container, Tag):
        return set()
    canonical = soup.find("link", rel="canonical")
    href = canonical.get("href") if isinstance(canonical, Tag) else None
    named = _GUIDE_RE.search(href) if isinstance(href, str) else None
    own = f"/guide/{named.group(1) if named else guide_slug}/"
    slugs: set[str] = set()
    for a in container.find_all("a"):
        href = a.get("href")
        slug = _slug_from(a) if isinstance(href, str) and own in href else None
        if slug and slug not in _SKIP_SLUGS:
            slugs.add(slug)
    return slugs


def app_title(slug: str, welcome: Path) -> str:
    """Guide title from welcome.html's hero <h1>; fallback to the slug."""
    if welcome.exists():
        soup = BeautifulSoup(welcome.read_text(encoding="utf-8", errors="ignore"), _PARSER)
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            return h1.get_text(" ", strip=True).replace("\xa0", " ")
    return slug.replace("-", " ").title()


def toc_items(welcome: Path, files_by_slug: dict[str, Path]) -> list[tuple[str, str, int]]:
    """Walk welcome's TOC tree -> ordered ('group', name, level) /
    ('topic', slug, level) items. level = min(2 + depth, 6) (app title is H1)."""
    soup = BeautifulSoup(welcome.read_text(encoding="utf-8", errors="ignore"), _PARSER)
    container = soup.find(id="modal-toc-container")
    root_ul = container.find("ul", class_="toc") if container else None
    items: list[tuple[str, str, int]] = []
    seen: set[str] = set()
    if root_ul is None:
        return items

    def walk(ul: Tag, depth: int) -> None:
        level = min(2 + depth, 6)
        for li in ul.find_all("li", recursive=False):
            a = li.find("a", recursive=False)
            button = li.find("button", recursive=False)
            sub = li.find("ul", recursive=False)
            slug = _slug_from(a) if a else None
            is_topic = bool(
                slug and slug not in _SKIP_SLUGS and slug in files_by_slug and slug not in seen
            )
            if sub is not None:
                if is_topic:
                    assert slug is not None
                    seen.add(slug)
                    items.append(("topic", slug, level))
                else:
                    name = (
                        button.get_text(" ", strip=True)
                        if button
                        else a.get_text(" ", strip=True)
                        if a
                        else None
                    )
                    if name:
                        items.append(("group", name, level))
                walk(sub, depth + 1)
            elif is_topic:
                assert slug is not None
                seen.add(slug)
                items.append(("topic", slug, level))

    walk(root_ul, 0)
    return items


def _tidy_see_also(soup: BeautifulSoup, root: Tag) -> None:
    """Rebuild Apple's <div class="LinkUniversal"> cross-ref blocks, whose anchors
    sit side by side with no separator, as bulleted lists."""
    for lu in root.find_all("div", class_="LinkUniversal"):
        anchors = lu.find_all("a")
        if not anchors:
            continue
        ul = soup.new_tag("ul")
        for a in anchors:
            a.extract()
            li = soup.new_tag("li")
            li.append(a)
            ul.append(li)
        lu.append(ul)


def extract_body(page_html: str, target_level: int) -> str | None:
    """Inner HTML of #article-section with headings shifted so its title sits at
    ``target_level``. Drops the topic icon. None if there's no article body."""
    soup = BeautifulSoup(page_html, _PARSER)
    section = soup.find(id="article-section")
    if section is None:
        return None
    h1 = section.find("h1")
    if h1 is None:
        return None
    root = h1.parent
    if root is None:
        return None
    for fig in root.find_all("figure", class_="topicIcon"):
        fig.decompose()
    # A PDF download block is appended to every topic.
    for dl in root.select("div.LinkDownload"):
        dl.decompose()
    strip_scripts(root)
    flatten_responsive_images(root)
    shift = target_level - 1
    for h in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        level = int(h.name[1])
        h.name = f"h{min(level + shift, 6)}"
    _tidy_see_also(soup, root)
    return root.decode_contents()


def build_merged_html(in_dir: Path, slug: str) -> tuple[str, list[str]]:
    """Merge in_dir's saved pages into one HTML document; also returns the slugs whose body could
    not be extracted, which the crawl's ``pages`` still counts."""
    welcome = in_dir / "welcome.html"
    title = app_title(slug, welcome)
    files_by_slug = {p.stem: p for p in in_dir.glob("*.html") if p.name != "welcome.html"}

    items = toc_items(welcome, files_by_slug) if welcome.exists() else []
    toc_slugs = {s for kind, s, _ in items if kind == "topic"}
    leftovers = sorted(s for s in files_by_slug if s not in toc_slugs)

    parts = [f"<h1>{_html.escape(title)}</h1>"]
    dropped: list[str] = []
    for kind, value, level in items:
        if kind == "group":
            parts.append(f"<h{level}>{_html.escape(value)}</h{level}>")
            continue
        inner = extract_body(
            files_by_slug[value].read_text(encoding="utf-8", errors="ignore"), level
        )
        if inner is None:
            dropped.append(value)
        else:
            parts.append(f'<section data-slug="{value}">\n{inner}\n</section>')
    for leftover in leftovers:
        inner = extract_body(
            files_by_slug[leftover].read_text(encoding="utf-8", errors="ignore"), 2
        )
        if inner is None:
            dropped.append(leftover)
        else:
            parts.append(f'<section data-slug="{leftover}">\n{inner}\n</section>')

    doc = (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        f"<title>{_html.escape(title)}</title></head>\n<body>\n"
        + "\n".join(parts)
        + "\n</body></html>\n"
    )
    return doc, dropped
