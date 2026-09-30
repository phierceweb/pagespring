"""One Hugo page: its content fragment, the reading order its sidebar gives, and
whether it is a generated view, a list of other pages or an empty section rather
than a page of the manual.

Content lives in ``<main>`` across the Hugo docs themes, and in Docsy's
``div.td-content`` inside it.
"""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag

from pagespring.patterns._nav_order import page_key
from pagespring.patterns._site import absolutize_refs, flatten_responsive_images, strip_scripts

# Most specific first: a Docsy site layout may repeat the title and add a TOC
# around td-content, inside <main>.
_CONTAINERS = ("div.td-content", "main")
# Theme chrome inside the content container. Most themes render the whole chapter
# list into every page, often in a plain <div> rather than a <nav>. An unlisted
# theme's chrome simply survives, which is the safe failure.
_CHROME_CSS = ", ".join(
    (
        "nav, header:not(.gdoc-post__header), footer",
        "div.drawer, div.book-menu, div.td-sidebar, aside.sidebar, #sidebar",
        # Docsy: feedback widget, child-page index, last-modified line, edit links,
        # heading anchors.
        "div.td-content > div.d-print-none, div.td-content > div.section-index",
        "div.td-content > div.td-page-meta__lastmod",
        "div.td-content > div.text-muted.mt-5.pt-3.border-top",
        "div.td-page-meta, a.td-heading-self-link",
        # Hugo Book
        "aside.book-menu, aside.book-toc, label.book-menu-overlay",
        ":is(h1, h2, h3, h4, h5, h6) > a.anchor",
        # Geekdoc
        "aside.gdoc-nav, div.gdoc-page__header, div.gdoc-page__footer, a.gdoc-page__anchor",
        "div.gdoc-post__meta",
    )
)
# Sidebars that list the whole manual in reading order.
_SIDEBAR_CSS = "nav.td-sidebar-nav, aside.book-menu, aside.gdoc-nav, div.drawer, #R-sidebar"
# Hugo Book's `layout: book` renders every subsection into one page.
_WHOLE_SECTION_CLASS = "book-layout-book"
_MEDIA = ("img", "svg", "video", "iframe")
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


def sidebar(soup: BeautifulSoup, page_url: str) -> list[str]:
    """The page keys the theme's sidebar links to, in order ([] without one)."""
    nav = soup.select_one(_SIDEBAR_CSS)
    if nav is None:
        return []
    hrefs = [a.get("href") for a in nav.find_all("a")]
    keys = (
        page_key(urljoin(page_url, h))
        for h in hrefs
        if isinstance(h, str) and h and not h.startswith(("#", "javascript:", "mailto:"))
    )
    return list(dict.fromkeys(keys))


def is_whole_section_view(soup: BeautifulSoup) -> bool:
    body = soup.body
    return body is not None and _WHOLE_SECTION_CLASS in body.get_attribute_list("class")


def extract(soup: BeautifulSoup, page_url: str) -> str | None:
    """The page's content container as a cleaned, absolutized fragment (None if absent)."""
    for css in _CONTAINERS:
        root = soup.select_one(css)
        if isinstance(root, Tag):
            break
    else:
        return None
    for el in root.select(_CHROME_CSS):
        el.decompose()
    strip_scripts(root)
    flatten_responsive_images(root)
    absolutize_refs(root, page_url)
    return str(root)


def _shows_nothing(root: Tag) -> bool:
    return not root.get_text(strip=True) and root.find(_MEDIA) is None


def is_empty(fragment: str) -> bool:
    """True when ``fragment`` shows a reader nothing: no text and no media."""
    return _shows_nothing(BeautifulSoup(fragment, "html.parser"))


def _entry_target(article: Tag, page_url: str) -> str | None:
    """The page a list entry's title links to; None when ``article`` is not an entry
    (an article that opens with a heading made of one link to another page)."""
    heading = article.find(_HEADINGS)
    link = heading.find("a") if isinstance(heading, Tag) else None
    if not isinstance(heading, Tag) or not isinstance(link, Tag):
        return None
    title = heading.get_text(strip=True)
    href = link.get("href")
    if link.get_text(strip=True) != title or not article.get_text(strip=True).startswith(title):
        return None
    target = page_key(urljoin(page_url, href)) if isinstance(href, str) else None
    return None if target == page_key(page_url) else target


def listed_pages(fragment: str, page_url: str) -> set[str]:
    """The page keys a list page's entries link to, or an empty set when the
    fragment holds anything besides those entries."""
    root = BeautifulSoup(fragment, "html.parser")
    targets: set[str] = set()
    for article in root.find_all("article"):
        if article.find_parent("article") is not None:
            continue
        target = _entry_target(article, page_url)
        if target is None:
            return set()
        targets.add(target)
        article.decompose()
    return targets if _shows_nothing(root) else set()
