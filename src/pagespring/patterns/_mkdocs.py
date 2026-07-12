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
    bodies: dict[str, list[str]] = {}
    order: list[str] = []
    for rec in records:
        loc = str(rec.get("location", ""))
        path, _, anchor = loc.partition("#")
        if path not in bodies:
            bodies[path] = []
            order.append(path)
        heading = "##" if anchor else "#"
        rec_title = str(rec.get("title", "")).strip()
        text = str(rec.get("text", "")).strip()
        block = f"{heading} {rec_title}\n\n{text}" if rec_title else text
        if block.strip():
            bodies[path].append(block)

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
