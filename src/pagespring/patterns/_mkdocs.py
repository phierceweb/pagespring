"""MkDocs acquisition for docs_probe — the search-index shortcut.

MkDocs ships a client-side search index at ``search/search_index.json``: a
``docs`` array of ``{location, title, text}`` records covering every page
(page-level records have no ``#`` anchor; section records carry one). One fetch
replaces a crawl. Known limitation: the index text is flattened plain text —
code blocks lose their fencing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult

log = get_logger(__name__)


def acquire(base_url: str, workdir: Path, *, slug: str, title: str | None) -> AcquireResult:
    base = base_url.rstrip("/")
    idx_url = f"{base}/search/search_index.json"
    final_url, body = http.fetch_text(idx_url)
    # Pages live relative to where the index actually resolved, not the URL we
    # asked for — a redirect (e.g. to /en/latest/) would otherwise stamp stale
    # source comments.
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
        # The page-level record holds the WHOLE page: lead prose plus every
        # section's text again. Keep only the prose before the first section,
        # or half the deliverable is a verbatim second copy of itself.
        if sections and lead:
            first = sections[0][1]
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
