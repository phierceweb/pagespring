"""One Writerside topic as a clean fragment: ``article.article``, or a starting page from its
``data-topic`` JSON, with client-rendered blocks rebuilt as plain HTML."""

from __future__ import annotations

import json
import textwrap
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag
from pf_core.log import get_logger

from pagespring import http
from pagespring.patterns._mathjax import rebuild_math
from pagespring.patterns._site import absolutize_refs, flatten_responsive_images, strip_scripts

log = get_logger(__name__)

MAX_HEADING = 6
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_CHROME_CSS = "div.last-modified, div.navigation-links, [data-feedback-placeholder]"
# Where a runnable sample's playground folds the code; the reader never sees them.
_PLAYGROUND_FOLDS = {"//sampleStart", "//sampleEnd"}
# Dark-theme variants, the linked topic's summary repeated on every link, and
# label ids once their names are written into the heading.
_CLIENT_ATTRS = (
    "data-dark-src",
    "data-dark-href",
    "data-dark-gif-src",
    "data-gif-src",
    "data-tooltip",
    "data-label-id",
    "data-annotation-ids",
)


class LabelNames:
    """Heading label names from the instance's ``config.json``, fetched on first use: a primary
    label shows its name, a secondary its short name, as the help app renders them."""

    def __init__(self, instance: str) -> None:
        self._url = f"{instance}config.json"
        self._names: dict[str, tuple[str, str]] | None = None

    def _load(self) -> dict[str, tuple[str, str]]:
        if self._names is not None:
            return self._names
        self._names = {}
        http.polite_sleep()
        try:
            _final, raw = http.fetch_text(self._url)
            labels: Any = json.loads(raw)["labels"]
        except Exception as exc:
            log.warning("writerside.labels_unreadable", url=self._url, error=str(exc))
            return self._names
        for label_id, spec in labels.items() if isinstance(labels, dict) else []:
            if isinstance(spec, dict):
                name = str(spec.get("name") or "").strip()
                short = str(spec.get("abbreviation") or "").strip()
                self._names[str(label_id)] = (name or short, short or name)
        return self._names

    def for_heading(self, primary: str, secondary: list[str]) -> list[str]:
        names = self._load()
        found = [names[primary][0]] if primary in names else []
        return found + [names[s][1] for s in secondary if s in names]


def _tag(soup: BeautifulSoup, name: str, text: str | None = None, **attrs: str) -> Tag:
    tag = soup.new_tag(name, attrs=attrs)
    if text is not None:
        tag.string = text
    return tag


def _label(soup: BeautifulSoup, text: str) -> Tag:
    para = _tag(soup, "p")
    para.append(_tag(soup, "strong", text))
    return para


def _is_button_row(nav: Tag) -> bool:
    """A list holding nothing but button links — the tour-style step navigation."""
    items = nav.find_all("li", recursive=False)
    for li in items:
        buttons = li.select('a[as="button"]')
        if len(buttons) != 1 or li.get_text(strip=True) != buttons[0].get_text(strip=True):
            return False
    return bool(items)


def _rebuild_client_blocks(soup: BeautifulSoup, root: Tag) -> None:
    for block in root.select("div.code-block, div.code-collapse"):
        lang = str(block.get("data-lang") or "")
        text = block.get_text()
        if block.get("data-runnable") == "true":
            lines = text.split("\n")
            text = "\n".join(line for line in lines if line.strip() not in _PLAYGROUND_FOLDS)
        code = _tag(soup, "code", textwrap.dedent(text).strip("\n").rstrip())
        if lang:
            code["class"] = f"language-{lang}"
        pre = _tag(soup, "pre")
        pre.append(code)
        synopsis = str(block.get("data-synopsis") or "").strip()
        if synopsis:
            block.insert_before(_tag(soup, "p", synopsis))
        block.replace_with(pre)
    for tab in root.select("div.tabs__content[data-title]"):
        tab_title = str(tab.get("data-title") or "").strip()
        if tab_title:
            tab.insert(0, _label(soup, tab_title))
    for aside in root.select("aside.prompt"):
        kind = str(aside.get("data-type") or "").strip().capitalize()
        callout = str(aside.get("data-title") or "").strip() or kind
        if callout:
            aside.insert(0, _label(soup, f"{callout}:"))
    for micro in root.select("div.micro-format"):
        try:
            parts = json.loads(str(micro.get("data-content") or ""))["microFormat"]
        except (ValueError, TypeError, KeyError):
            micro.decompose()
            continue
        box = _tag(soup, "div", **{"class": "tldr"})
        for part in parts if isinstance(parts, list) else []:
            box.append(BeautifulSoup(str(part), "html.parser"))
        micro.replace_with(box)
    for player in root.select("div.video-player"):
        obj = player.find("object")
        src = obj.get("data") if isinstance(obj, Tag) else None
        if isinstance(src, str) and src:
            para = _tag(soup, "p")
            para.append(_tag(soup, "a", "Video", href=src))
            player.replace_with(para)


def _name_labels(root: Tag, labels: LabelNames) -> None:
    for h in root.find_all(_HEADINGS):
        primary = str(h.get("data-label-id") or "")
        secondary = [s for s in str(h.get("data-annotation-ids") or "").split(",") if s]
        if primary or secondary:
            names = labels.for_heading(primary, secondary)
            if names:
                h.append(" " + " ".join(f"[{n}]" for n in names))


def _clean(soup: BeautifulSoup, root: Tag, page_url: str, labels: LabelNames) -> None:
    for el in root.select(_CHROME_CSS):
        el.decompose()
    for nav in root.find_all(["ul", "ol"]):
        if isinstance(nav, Tag) and not nav.decomposed and _is_button_row(nav):
            nav.decompose()
    for table in root.find_all("table"):
        if isinstance(table, Tag) and table.find("tr") is None:
            (table.find_parent("div", class_="table-wrapper") or table).decompose()
    strip_scripts(root)
    rebuild_math(root)
    _rebuild_client_blocks(soup, root)
    _name_labels(root, labels)
    for img in root.select("img[data-gif-src]"):
        if not img.get("src"):
            img["src"] = str(img["data-gif-src"])
    flatten_responsive_images(root)
    for tag in root.find_all(True):
        for attr in _CLIENT_ATTRS:
            if tag.has_attr(attr):
                del tag[attr]
    absolutize_refs(root, page_url)


def _squash(value: object) -> str:
    return " ".join(str(value or "").split())


def _card(soup: BeautifulSoup, item: dict[str, Any], page_url: str) -> Tag | None:
    title = _squash(item.get("title"))
    about = _squash(item.get("description"))
    if not title and not about:
        return None
    li = _tag(soup, "li")
    url = item.get("url")
    if title:
        href = urljoin(page_url, url) if isinstance(url, str) and url else None
        li.append(_tag(soup, "a", title, href=href) if href else title)
    if about:
        li.append(f" — {about}" if title else about)
    return li


def _cards(soup: BeautifulSoup, out: Tag, block: dict[str, Any], page_url: str, level: int) -> None:
    """A card block as a list of links with their descriptions, under its title
    when it has one; a nested block's title sits one level lower."""
    items = block.get("data")
    if not isinstance(items, list) or not items:
        return
    title = _squash(block.get("title"))
    if title:
        out.append(_tag(soup, f"h{level}", title))
        level = min(level + 1, MAX_HEADING)
    listing: Tag | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("data"), list):
            _cards(soup, out, item, page_url, level)
            listing = None
            continue
        li = _card(soup, item, page_url)
        if li is None:
            continue
        if listing is None:
            listing = _tag(soup, "ul")
            out.append(listing)
        listing.append(li)


def _section_page(soup: BeautifulSoup, page_url: str, fallback_title: str) -> Tag:
    """A starting page rebuilt from its ``data-topic`` JSON: title, subtitle, and
    each card block (tips, main, highlighted, groups) with its descriptions."""
    body = soup.body
    attrs = body.attrs if isinstance(body, Tag) else {}
    heading = str(attrs.get("data-main-title") or "").strip() or fallback_title
    article = _tag(soup, "article", **{"class": "article"})
    article.append(_tag(soup, "h1", heading))
    topic = attrs.get("data-topic")
    if not isinstance(topic, str) or not topic:
        return article
    http.polite_sleep()
    try:
        _final, raw = http.fetch_text(urljoin(page_url, topic))
        data = json.loads(raw)
    except Exception as exc:
        log.warning("writerside.starting_page_error", url=page_url, error=str(exc))
        return article
    if not isinstance(data, dict):
        return article
    title = _squash(data.get("title"))
    subtitle = _squash(data.get("subtitle"))
    if title and title.lower() != heading.lower():
        article.append(_tag(soup, "p", title))
    if subtitle:
        article.append(_tag(soup, "p", subtitle))
    groups = data.get("groups")
    blocks = [{"data": data.get("tips")}, data.get("main"), data.get("highlighted")]
    for block in [*blocks, *(groups if isinstance(groups, list) else [])]:
        if isinstance(block, dict):
            _cards(soup, article, block, page_url, 2)
    return article


def extract(page: str, page_url: str, *, title: str, labels: LabelNames) -> Tag | None:
    """The topic as a cleaned, absolutized ``<article>``; None when the page is
    neither an article nor a starting page."""
    soup = BeautifulSoup(page, "html.parser")
    article = soup.select_one("article.article")
    if isinstance(article, Tag):
        _clean(soup, article, page_url, labels)
        return article
    body = soup.body
    if isinstance(body, Tag) and body.get("data-template") == "section-page":
        return _section_page(soup, page_url, title)
    return None


def shift_headings(root: Tag, depth: int) -> None:
    """Push every heading ``depth`` levels down, capped at h6."""
    for h in root.find_all(_HEADINGS):
        h.name = f"h{min(int(h.name[1]) + depth, MAX_HEADING)}"
