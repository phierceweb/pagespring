"""Render a Postman collection (schema v2.1) to clean markdown — one section per
request, folders as nested headings. Pure transformation over a parsed dict.
"""

from __future__ import annotations

from typing import Any


def count_requests(coll: dict[str, Any]) -> int:
    """Number of requests across all folders."""

    def _count(items: list[Any]) -> int:
        total = 0
        for it in items:
            if isinstance(it, dict) and isinstance(it.get("item"), list):
                total += _count(it["item"])
            elif isinstance(it, dict) and "request" in it:
                total += 1
        return total

    items = coll.get("item", [])
    return _count(items) if isinstance(items, list) else 0


def render(coll: dict[str, Any], title: str) -> str:
    """Collection dict → markdown document."""
    out: list[str] = [f"# {title}"]
    info = coll.get("info", {})
    if isinstance(info, dict) and info.get("description"):
        desc = info["description"]
        out.append(desc.get("content", "") if isinstance(desc, dict) else str(desc))
    items = coll.get("item", [])
    if isinstance(items, list):
        out.extend(_walk(items, depth=2))
    return "\n\n".join(s for s in out if s) + "\n"


def _walk(items: list[Any], depth: int) -> list[str]:
    out: list[str] = []
    hashes = "#" * min(depth, 6)
    for it in items:
        if not isinstance(it, dict):
            continue
        if isinstance(it.get("item"), list):  # folder
            out.append(f"{hashes} {it.get('name', '')}")
            out.extend(_walk(it["item"], depth + 1))
        elif "request" in it:
            out.append(_render_request(it, depth))
    return out


def _render_request(it: dict[str, Any], depth: int) -> str:
    req = it.get("request", {})
    req = req if isinstance(req, dict) else {}
    method = req.get("method", "")
    url = _url(req.get("url"))
    parts = [f"{'#' * min(depth, 6)} {it.get('name', '')}", f"`{method} {url}`"]

    headers = req.get("header", [])
    if isinstance(headers, list) and headers:
        hs = "\n".join(
            f"- `{h.get('key')}: {h.get('value', '')}`" for h in headers if isinstance(h, dict)
        )
        parts.append("**Headers:**\n\n" + hs)

    body = req.get("body", {})
    raw = body.get("raw") if isinstance(body, dict) else None
    if raw:
        parts.append("**Body:**\n\n```\n" + str(raw).strip() + "\n```")

    examples = it.get("response", [])
    if isinstance(examples, list):
        for resp in examples:
            if isinstance(resp, dict):
                parts.append(f"_Example response: {resp.get('name', '')} ({resp.get('code', '')})_")
    return "\n\n".join(parts)


def _url(url: Any) -> str:
    if isinstance(url, str):
        return url
    if isinstance(url, dict):
        return str(url.get("raw", ""))
    return ""
