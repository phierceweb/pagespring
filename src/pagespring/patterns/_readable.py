"""Main-content extraction for crawled pages: ``main_content`` finds the node and ``clean`` reduces
it to what a reader sees, with tab labels, code text and MathML kept."""

from __future__ import annotations

from bs4 import BeautifulSoup
from bs4.element import Tag

from pagespring.patterns._mathjax import rebuild_math
from pagespring.patterns._shiki import flatten_shiki
from pagespring.patterns._site import absolutize_refs, flatten_responsive_images, strip_scripts

_CHROME_CSS = "nav, footer, [role=navigation], [role=search], [role=contentinfo]"
_UNSEEN_CSS = "link, template, .sr-only, .visually-hidden, .screen-reader-text"
_HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
_TEXT_BLOCKS = ["p", "pre", "ul", "ol", "table", "blockquote", "dl", "h1", "h2", "h3", "h4"]


def _text_len(node: Tag) -> int:
    return len(node.get_text(" ", strip=True))


def _top_level_articles(root: Tag) -> list[Tag]:
    return [a for a in root.find_all("article") if a.find_parent("article") is None]


def main_content(soup: Tag) -> Tag | None:
    """The page's content node, most specific evidence first; an ``<article>`` in ``<main>`` wins
    only with most of main's text, since several articles are landing-page cards."""
    main = soup.find("main")
    if not isinstance(main, Tag):
        main = soup.select_one('[role="main"]')
    if isinstance(main, Tag):
        articles = _top_level_articles(main)
        if len(articles) == 1 and 2 * _text_len(articles[0]) >= _text_len(main):
            return articles[0]
        return main
    articles = _top_level_articles(soup)
    if len(articles) == 1:
        return articles[0]
    return _densest_block(soup)


def _densest_block(soup: Tag) -> Tag | None:
    """The block holding the most text in its own paragraphs, lists and code."""
    best: Tag | None = None
    best_len = 0
    for el in soup.find_all(["body", "div", "section", "td"]):
        n = sum(_text_len(c) for c in el.find_all(_TEXT_BLOCKS, recursive=False))
        if n > best_len:
            best, best_len = el, n
    return best


def clean(node: Tag, page_url: str) -> None:
    """Reduce an extracted content node, in place, to what a reader sees."""
    strip_scripts(node)
    rebuild_math(node)  # before hidden SVGs go: MathJax hides its drawing once it has MathML
    _label_tab_panels(node)  # before buttons go: a tab is often a <button>
    for el in node.select(_UNSEEN_CSS):
        if not el.decomposed:
            el.decompose()
    for button in node.find_all("button"):
        if button.decomposed:
            continue
        if _heads_a_disclosure(button):
            button.unwrap()
        else:
            button.decompose()
    for a in node.select("a[href^='#']"):
        if not a.decomposed and _is_permalink(a):
            a.decompose()
    for svg in node.find_all("svg"):
        if svg.decomposed:
            continue
        if svg.get("aria-hidden") == "true" or svg.find_parent(attrs={"aria-hidden": "true"}):
            svg.decompose()
    _join_code_lines(node)
    flatten_shiki(node)
    flatten_responsive_images(node)
    absolutize_refs(node, page_url)


def _heads_a_disclosure(button: Tag) -> bool:
    """An accordion's or a kept tab strip's label: text in a heading or a control that
    names the region it opens. Any other button is a control (copy, theme, search)."""
    if not button.get_text(strip=True):
        return False
    return button.find_parent(_HEADINGS) is not None or any(
        button.has_attr(a) for a in ("aria-expanded", "aria-controls")
    )


def _is_permalink(a: Tag) -> bool:
    """A heading's own anchor link: a known permalink class, or a symbol standing alone."""
    classes = set(a.get_attribute_list("class"))
    if classes & {"headerlink", "header-anchor", "hash-link", "anchor-link", "anchorjs-link"}:
        return True
    text = a.get_text(strip=True)
    return len(text) <= 1 and not text.isalnum() and a.find("img") is None


def _join_code_lines(root: Tag) -> None:
    """One plain ``<pre><code>`` per Expressive Code block, its lines newline-joined."""
    for pre in root.find_all("pre"):
        lines = pre.select("div.ec-line")
        if not lines:
            continue
        text = "\n".join(_line_text(line) for line in lines)
        code = pre.find("code")
        target = code if isinstance(code, Tag) else pre
        target.clear()
        target.append(text)
        lang = pre.get("data-language")
        if isinstance(code, Tag) and isinstance(lang, str) and lang:
            code["class"] = f"language-{lang}"
    for copy in root.select("div.expressive-code div.copy"):
        copy.decompose()
    for caption in root.select("div.expressive-code figcaption"):
        if not caption.get_text(strip=True):
            caption.decompose()


def _line_text(line: Tag) -> str:
    """A line's code without its gutter; a blank line renders as a lone newline."""
    code = line.find("div", class_="code")
    return (code if isinstance(code, Tag) else line).get_text().removesuffix("\n")


def _label_tab_panels(root: Tag) -> None:
    """Show every tab panel under its tab's label; the strip goes only once every tab labels a
    panel, since otherwise it holds the only copy of some labels."""
    factory = BeautifulSoup("", "html.parser")
    used: set[int] = set()
    for panel in root.select('[role="tabpanel"]'):
        if panel.has_attr("hidden"):
            del panel["hidden"]
        tab = _tab_for(root, panel)
        label = tab.get_text(" ", strip=True) if tab is not None else ""
        if tab is None or not label:
            continue
        heading = factory.new_tag("p")
        strong = factory.new_tag("strong")
        strong.string = label
        heading.append(strong)
        panel.insert(0, heading)
        used.add(id(tab))
    for tablist in root.select('[role="tablist"]'):
        tabs = tablist.select('[role="tab"]')
        if tabs and all(id(t) in used for t in tabs):
            tablist.decompose()


def _tab_for(root: Tag, panel: Tag) -> Tag | None:
    labelled_by = panel.get("aria-labelledby")
    if isinstance(labelled_by, str) and labelled_by:
        tab = root.find(id=labelled_by)
        if isinstance(tab, Tag):
            return tab
    panel_id = panel.get("id")
    if isinstance(panel_id, str) and panel_id:
        return next(
            (t for t in root.select('[role="tab"]') if t.get("aria-controls") == panel_id), None
        )
    return None


def extract_main(html: str, page_url: str) -> str | None:
    """The page's main content without site chrome, cleaned; None when it has no text."""
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.select(_CHROME_CSS):
        if not el.decomposed:
            el.decompose()
    node = main_content(soup)
    if node is None:
        return None
    clean(node, page_url)
    return str(node) if node.get_text(strip=True) else None
