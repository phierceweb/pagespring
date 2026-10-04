"""One Flare topic as a clean fragment: ``#mc-main-content`` without the help runtime's controls."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from pagespring.patterns._site import absolutize_refs, flatten_responsive_images, strip_scripts

_CODE_LANG_RE = re.compile(r"mc-code-lang:\s*([\w+#.-]+)", re.I)


def _help_controls(node: Tag) -> None:
    """A related-topics control becomes the links it lists; one the runtime fills from the index
    (keywords, concepts) shows nothing here and goes."""
    maker = BeautifulSoup("", "html.parser")
    for control in node.select("a.MCHelpControl"):
        listed = control.get("data-mc-topics")
        pairs = (
            [pair.split("|", 1) for pair in listed.split("||")] if isinstance(listed, str) else []
        )
        links = [(p[0].strip(), p[1].strip()) for p in pairs if len(p) == 2 and p[0] and p[1]]
        if not links:
            control.decompose()
            continue
        span = maker.new_tag("span")
        span.append(f"{control.get_text(' ', strip=True) or 'Related Topics'}: ")
        for i, (title, href) in enumerate(links):
            if i:
                span.append(", ")
            link = maker.new_tag("a", href=href)
            link.string = title
            span.append(link)
        control.replace_with(span)


def _code_snippets(node: Tag) -> None:
    """A code snippet loses its copy button and highlighting: plain lines, its language kept."""
    maker = BeautifulSoup("", "html.parser")
    for snippet in node.select("div.codeSnippet"):
        pre = snippet.find("pre")
        if not isinstance(pre, Tag):
            continue
        for br in pre.find_all("br"):
            br.replace_with("\n")
        body = snippet.select_one(".codeSnippetBody")
        style = body.get("style") if isinstance(body, Tag) else None
        lang = _CODE_LANG_RE.search(style) if isinstance(style, str) else None
        code = maker.new_tag("code")
        if lang:
            code["class"] = f"language-{lang.group(1).lower()}"
        code.string = pre.get_text()
        block = maker.new_tag("pre")
        block.append(code)
        snippet.replace_with(block)


def _clean(node: Tag, page_url: str) -> None:
    strip_scripts(node)
    _help_controls(node)
    _code_snippets(node)
    # A text popup's body is a hover definition; inline, it splits the sentence it annotates.
    for body in node.select(".MCTextPopupBody"):
        body.decompose()
    # Skin images are the runtime's own icons, such as a dropdown's transparent placeholder.
    for img in node.find_all("img"):
        src = img.get("src")
        if isinstance(src, str) and "/Skins/" in urlparse(urljoin(page_url, src)).path:
            img.decompose()
    # Dropdown, toggler and popup hotspots link to script or a bare "#"; their text is the label.
    for link in node.find_all("a"):
        href = link.get("href")
        target = href.strip().lower() if isinstance(href, str) else None
        if target is not None and (target == "#" or target.startswith("javascript:")):
            link.unwrap()
    flatten_responsive_images(node)
    absolutize_refs(node, page_url)


def extract(page: str, page_url: str) -> Tag | None:
    """The topic body, cleaned and absolutized; None when the page has none."""
    node = BeautifulSoup(page, "html.parser").find(id="mc-main-content")
    if not isinstance(node, Tag):
        return None
    _clean(node, page_url)
    return node
