"""One Fluid Topics topic as a clean fragment: its TOC title as the heading, inline images bundled
as files, internal links pointed at reader pages, screen-reader text gone."""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path
from urllib.parse import unquote_to_bytes

from bs4 import BeautifulSoup
from pf_core.log import get_logger
from pf_core.utils.hashing import content_hash

from pagespring.base import IMAGES_DIR
from pagespring.patterns._archive_images import claim_name
from pagespring.patterns._site import (
    absolutize_refs,
    flatten_responsive_images,
    seat_headings,
    strip_scripts,
    toc_heading,
)

log = get_logger(__name__)

_DATA_URI_RE = re.compile(r"^data:image/([\w.+-]+)((?:;[^,;]*)*),(.*)$", re.S | re.I)
_EXTENSIONS = {"jpeg": "jpg", "svg+xml": "svg"}


def _decode(params: str, payload: str) -> bytes | None:
    """A data URI's bytes: base64 when its parameters say so, else percent-encoded text."""
    if "base64" not in {param.strip().lower() for param in params.split(";")}:
        return unquote_to_bytes(payload)
    try:
        return base64.b64decode(re.sub(r"\s+", "", payload), validate=True)
    except (binascii.Error, ValueError):
        return None


class ImageBundle:
    """Inline images written once each into ``dest``, named for their display name."""

    def __init__(self, dest: Path) -> None:
        self._dest = dest
        self._names: dict[str, str] = {}
        self._taken: set[str] = set()

    def ref(self, data_uri: str, display: object) -> str | None:
        """The ``images/<name>`` ref for an image data URI; None when it does not decode."""
        found = _DATA_URI_RE.match(data_uri.strip())
        if found is None:
            return None
        data = _decode(found.group(2), found.group(3))
        if not data:
            return None
        digest = content_hash(data)
        name = self._names.get(digest)
        if name is None:
            subtype = found.group(1).lower()
            stem = display.rsplit(".", 1)[0] if isinstance(display, str) and display else "image"
            name = self._names[digest] = claim_name(
                f"{stem}.{_EXTENSIONS.get(subtype, subtype)}", self._taken
            )
            self._dest.mkdir(parents=True, exist_ok=True)
            (self._dest / name).write_bytes(data)
        return f"{IMAGES_DIR}/{name}"


def _link_topics(soup: BeautifulSoup, links: dict[str, str]) -> None:
    """An internal link is a span naming a TOC node; it becomes a link to that node's reader page."""
    for span in soup.select("span.ft-internal-link"):
        target = links.get(str(span.get("data-tocid") or ""))
        if target is None:
            span.unwrap()
            continue
        section = span.get("data-section")
        link = soup.new_tag("a", href=f"{target}#{section}" if section else target)
        link.extend(list(span.contents))
        span.replace_with(link)


def _bundle_images(soup: BeautifulSoup, bundle: ImageBundle, *, page_url: str) -> None:
    for img in soup.find_all("img"):
        src = img.get("src")
        if isinstance(src, str) and src.startswith("data:"):
            ref = bundle.ref(src, img.get("data-ft-asset-display-name"))
            if ref is None:
                log.warning("fluidtopics.image_undecodable", url=page_url, uri=src[:40])
                img.decompose()
            else:
                img["src"] = ref


def clean_topic(
    fragment: str,
    *,
    page_url: str,
    title: str,
    depth: int,
    links: dict[str, str],
    bundle: ImageBundle,
) -> BeautifulSoup | None:
    """The topic headed by its TOC ``title`` at ``depth``; None when its content is empty."""
    soup = BeautifulSoup(fragment, "html.parser")
    strip_scripts(soup)
    for hidden in soup.select(".u-sr-only"):
        hidden.decompose()
    if not soup.get_text(strip=True) and soup.find("img") is None:
        return None
    _link_topics(soup, links)
    absolutize_refs(soup, page_url)
    _bundle_images(soup, bundle, page_url=page_url)
    flatten_responsive_images(soup)
    # Again for a lazy or srcset winner flatten promoted; bundle refs stay relative for staging.
    absolutize_refs(soup, page_url, keep=(f"{IMAGES_DIR}/",))
    seat_headings(soup, depth=depth + 1, name="")
    soup.insert(0, BeautifulSoup(toc_heading(depth, title), "html.parser"))
    return soup
