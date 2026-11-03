"""MkDocs for docs_probe: every page from ``search/search_index.json`` in one fetch. The index text
is flattened, so code blocks lose their fencing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from pw_foundation.exceptions import InvalidInputError
from pw_foundation.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns._site import names_a_file
from pagespring.patterns._soup import parse_html

log = get_logger(__name__)

# A statement's start (a line's, or after ``;``) outside block comments: a line comment or a string
# that mentions the assignment is not it.
_CLASSIC_BASE = re.compile(r"""(?:^|;)\s*var\s+base_url\s*=\s*["']([^"']*)["']""", re.MULTILINE)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _named_bases(home: str) -> list[object]:
    soup = parse_html(home)
    config = soup.find("script", id="__config")
    if config is not None:
        try:
            return [json.loads(config.get_text()).get("base")]
        except (ValueError, AttributeError):
            return []
    scripts = (_BLOCK_COMMENT.sub("", s.get_text()) for s in soup.find_all("script"))
    return [m.group(1) for text in scripts for m in _CLASSIC_BASE.finditer(text)]


def site_root(home: str, page_url: str) -> str | None:
    """The site root a page names, resolved against it: Material's ``__config`` ``base``, or the
    classic themes' ``base_url``. None unless it is the page's directory or above, on its origin."""
    page = urlparse(page_url)
    last = page.path.rsplit("/", 1)[-1]
    if last and not names_a_file(last):
        page = page._replace(path=f"{page.path}/")
    for base in _named_bases(home):
        if not isinstance(base, str):
            continue
        root = urlparse(urljoin(page.geturl(), base))
        root_dir = root.path.rstrip("/") + "/"
        if (root.scheme, root.netloc) == (page.scheme, page.netloc) and page.path.startswith(
            root_dir
        ):
            return f"{root.scheme}://{root.netloc}{root_dir}".rstrip("/")
    return None


def acquire(base_url: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    base = base_url.rstrip("/")
    idx_url = f"{base}/search/search_index.json"
    final_url, body = http.fetch_text(idx_url)
    # Resolve pages against where the index landed, not the URL asked for: a redirect would stamp
    # stale source comments.
    suffix = "/search/search_index.json"
    final_base = final_url[: -len(suffix)] if final_url.endswith(suffix) else base
    try:
        records: list[dict[str, Any]] = json.loads(body)["docs"]
    except (ValueError, TypeError, KeyError) as exc:
        raise InvalidInputError(f"{idx_url} is not a MkDocs search index") from exc
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise InvalidInputError(f"{idx_url} 'docs' is not a list of records")

    # Group section records (location has a #anchor) under their page.
    pages: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for rec in records:
        loc = str(rec.get("location", ""))
        path, _, anchor = loc.partition("#")
        if path not in pages:
            pages[path] = {"title": "", "text": "", "sections": []}
            order.append(path)
        rec_title = str(rec.get("title", "")).strip()
        text = str(rec.get("text", "")).strip()
        if anchor:
            pages[path]["sections"].append((rec_title, text))
        else:
            pages[path]["title"] = rec_title
            pages[path]["text"] = text

    bodies: dict[str, list[str]] = {}
    for path, page in pages.items():
        sections: list[tuple[str, str]] = page["sections"]
        lead = page["text"]
        # The page-level record repeats every section's text after the lead prose; keep only the
        # prose before the first section, or the page appears twice.
        if sections and lead:
            # The first section *with text*: a heading straight above a sub-heading
            # indexes with none of its own.
            first = next((text for _title, text in sections if text), "")
            if first and first in lead:
                lead = lead.split(first, 1)[0].strip()
        blocks: list[str] = []
        if page["title"]:
            blocks.append(f"# {page['title']}" + (f"\n\n{lead}" if lead else ""))
        elif lead:
            blocks.append(lead)
        for sec_title, sec_text in sections:
            block = f"## {sec_title}\n\n{sec_text}" if sec_title else sec_text
            if block.strip():
                blocks.append(block)
        bodies[path] = blocks

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    for i, path in enumerate(order):
        page_md = "\n\n".join(bodies[path]).strip()
        if not page_md:
            continue
        stem = path.strip("/").replace("/", "-") or "index"
        (raw_dir / f"{i:04d}-{stem}.md").write_text(
            f"<!-- source: {final_base}/{path} -->\n\n{page_md}\n", encoding="utf-8"
        )
        saved += 1
    log.info("mkdocs.acquire", base=final_base, pages=saved, slug=slug)
    return AcquireResult(raw_dir=raw_dir, kind="markdown", slug=slug, pages=saved, title=title)
