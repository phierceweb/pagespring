"""Render a Postman v2.x collection dict to markdown, one section per request and folders as nested
headings; fields of the wrong type are ignored, not raised on."""

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
    info = coll.get("info")
    if isinstance(info, dict):
        out.append(_text(info.get("description")))
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
            out.append(_text(it.get("description")))
            out.extend(_walk(it["item"], depth + 1))
        elif "request" in it:
            out.append(_render_request(it, depth))
    return out


def _text(value: Any) -> str:
    """A Postman description: a string, or ``{"content": ...}``."""
    if isinstance(value, dict):
        value = value.get("content")
    return value.strip() if isinstance(value, str) else ""


def _render_request(it: dict[str, Any], depth: int) -> str:
    req = it.get("request")
    if isinstance(req, str):
        req = {"method": "GET", "url": req}
    req = req if isinstance(req, dict) else {}
    url = req.get("url")
    req_desc, item_desc = _text(req.get("description")), _text(it.get("description"))
    parts = [
        f"{'#' * min(depth, 6)} {it.get('name', '')}",
        f"`{req.get('method', '')} {_url(url)}`",
        req_desc,
        item_desc if item_desc != req_desc else "",
    ]
    if isinstance(url, dict):
        parts.append(_entries("Query parameters", url.get("query"), "="))
        parts.append(_entries("Path variables", url.get("variable"), ": "))
    parts.append(_entries("Headers", req.get("header"), ": "))
    parts.append(_body(req.get("body")))

    examples = it.get("response", [])
    if isinstance(examples, list):
        for resp in examples:
            if isinstance(resp, dict):
                parts.append(f"_Example response: {resp.get('name', '')} ({resp.get('code', '')})_")
    return "\n\n".join(p for p in parts if p)


def _entries(label: str, entries: Any, sep: str) -> str:
    """``- `key<sep>value` (note) — description`` per key/value entry, under a bold label."""
    lines: list[str] = []
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict) or not e.get("key"):
            continue
        value = e.get("value")
        is_file = e.get("type") == "file"
        if is_file or value is None or isinstance(value, (dict, list)):
            value = ""
        line = f"- `{e['key']}{sep}{value}`" if value != "" else f"- `{e['key']}`"
        if is_file:
            src = e.get("src")
            srcs = ", ".join(
                s for s in (src if isinstance(src, list) else [src]) if isinstance(s, str)
            )
            line += f" (file: {srcs})" if srcs else " (file)"
        if e.get("disabled") is True:
            line += " (disabled)"
        desc = _text(e.get("description"))
        lines.append(f"{line} — {desc}" if desc else line)
    return f"**{label}:**\n\n" + "\n".join(lines) if lines else ""


def _body(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    mode = body.get("mode", "raw")
    if mode == "raw":
        raw_opts = _dict(_dict(body.get("options")).get("raw"))
        lang = raw_opts.get("language")
        block = _fence(body.get("raw"), lang if isinstance(lang, str) else "")
        return f"**Body:**\n\n{block}" if block else ""
    if mode == "urlencoded":
        return _entries("Body (urlencoded)", body.get("urlencoded"), "=")
    if mode == "formdata":
        return _entries("Body (form-data)", body.get("formdata"), "=")
    if mode == "graphql":
        gql = _dict(body.get("graphql"))
        variables = gql.get("variables")
        blocks = [
            _fence(gql.get("query"), "graphql"),
            _fence(variables, "json") if variables != "{}" else "",
        ]
        shown = [b for b in blocks if b]
        return "\n\n".join(["**Body (GraphQL):**", *shown]) if shown else ""
    if mode == "file":
        src = _dict(body.get("file")).get("src")
        return f"**Body (file):** `{src}`" if isinstance(src, str) and src else ""
    return ""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _fence(code: Any, lang: str) -> str:
    if not isinstance(code, str) or not code.strip():
        return ""
    return f"```{lang}\n{code.strip()}\n```"


def _url(url: Any) -> str:
    if isinstance(url, str):
        return url
    if isinstance(url, dict):
        return str(url.get("raw", ""))
    return ""
