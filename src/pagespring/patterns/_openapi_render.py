"""Render an OpenAPI 3.x / Swagger 2.0 spec to clean markdown — one section per
operation, with params, request body, responses, and resolved ``$ref`` schemas.

Pure transformation over a parsed dict; no network, no file I/O. Fields of the
wrong type are ignored rather than raised on.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")


def count_operations(spec: dict[str, Any]) -> int:
    """Number of HTTP operations across all paths."""
    return sum(
        sum(1 for m in _METHODS if isinstance(item.get(m), dict)) for _, item in _path_items(spec)
    )


def render(spec: dict[str, Any], title: str) -> str:
    """Spec dict → markdown document."""
    is_v2 = str(spec.get("swagger", "")).startswith("2")
    out: list[str] = [f"# {title}"]
    info = spec.get("info", {})
    if isinstance(info, dict) and info.get("description"):
        out.append(str(info["description"]).strip())
    base = _base_url(spec, is_v2)
    if base:
        out.append(f"**Base URL:** `{base}`")
    out.append(_tags(spec))

    for path, item in _path_items(spec):
        common = _as_list(item.get("parameters"))
        for method in _METHODS:
            op = item.get(method)
            if isinstance(op, dict):
                out.append(_render_operation(spec, is_v2, method, path, op, common))
    return "\n\n".join(s for s in out if s) + "\n"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _path_items(spec: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """``(path, item)`` pairs, a ``$ref`` item resolved with its own keys taking precedence."""
    # An explicit `paths:` with nothing under it parses as None, not a missing key.
    paths = spec.get("paths")
    pairs: list[tuple[str, dict[str, Any]]] = []
    for path, item in (paths if isinstance(paths, dict) else {}).items():
        if not isinstance(item, dict):
            continue
        if "$ref" in item:
            local = {k: v for k, v in item.items() if k != "$ref"}
            item = {**_resolve_ref(spec, item), **local}
        pairs.append((str(path), item))
    return pairs


def _base_url(spec: dict[str, Any], is_v2: bool) -> str | None:
    if is_v2:
        host = spec.get("host") or ""
        base = spec.get("basePath") or ""
        schemes = spec.get("schemes")
        if isinstance(schemes, list) and schemes and isinstance(schemes[0], str):
            scheme = schemes[0]
        else:
            scheme = schemes if isinstance(schemes, str) and schemes else "https"
        return f"{scheme}://{host}{base}" if host else (str(base) or None)
    servers = _as_list(spec.get("servers"))
    if servers and isinstance(servers[0], dict) and servers[0].get("url"):
        return str(servers[0]["url"])
    return None


def _resolve_ref(spec: dict[str, Any], node: Any) -> dict[str, Any]:
    """Follow a chain of local ``{"$ref": "#/a/b"}`` pointers; external or cyclic refs → ``{}``."""
    seen: set[str] = set()
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            return {}
        seen.add(ref)
        node = spec
        for part in ref[2:].split("/"):
            key = unquote(part).replace("~1", "/").replace("~0", "~")
            node = node.get(key, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def _type_name(schema: dict[str, Any]) -> str:
    typ = schema.get("type")
    if isinstance(typ, list):
        return " | ".join(str(t) for t in typ)
    return str(typ) if typ else ""


def _schema_lines(spec: dict[str, Any], schema: Any) -> list[str]:
    """One bullet per property of a (possibly ``$ref``'d) object schema."""
    schema = _resolve_ref(spec, schema)
    props = schema.get("properties")
    if not isinstance(props, dict):
        t = _type_name(schema)
        return [f"- _{t}_"] if t else []
    required = {r for r in _as_list(schema.get("required")) if isinstance(r, str)}
    lines: list[str] = []
    for name, prop in props.items():
        prop = _resolve_ref(spec, prop)
        typ = _type_name(prop) or "object"
        req = " (required)" if name in required else ""
        desc = f" — {str(prop['description'])}" if prop.get("description") else ""
        lines.append(f"- `{name}` _{typ}_{req}{desc}")
    return lines


def _cell(value: object) -> str:
    """``value`` as one table cell: a line break would end the row, a bare ``|`` the cell."""
    return " ".join(str(value).splitlines()).replace("|", r"\|")


def _render_params(spec: dict[str, Any], params: list[Any]) -> str:
    rows: list[str] = []
    for p in params:
        p = _resolve_ref(spec, p)
        if p.get("in") == "body":  # v2 body param renders as a request body
            continue
        if not p.get("name"):
            continue  # unresolved/nameless param → skip the empty row
        typ = _type_name(_resolve_ref(spec, p.get("schema"))) or _type_name(p)
        req = "yes" if p.get("required") else "no"
        cells = (f"`{_cell(p['name'])}`", _cell(p.get("in", "")), _cell(typ), req)
        rows.append(f"| {' | '.join(cells)} | {_cell(p.get('description') or '')} |")
    if not rows:
        return ""
    head = "| Name | In | Type | Required | Description |\n| --- | --- | --- | --- | --- |"
    return "**Parameters:**\n\n" + head + "\n" + "\n".join(rows)


def _request_body(spec: dict[str, Any], is_v2: bool, op: dict[str, Any], params: list[Any]) -> str:
    if is_v2:
        resolved = (_resolve_ref(spec, p) for p in params)
        body = next((p for p in resolved if p.get("in") == "body"), {})
    else:
        body = _resolve_ref(spec, op.get("requestBody"))
        content = body.get("content")
        media: Any = next(iter(content.values()), {}) if isinstance(content, dict) else {}
        body = {**body, "schema": media.get("schema") if isinstance(media, dict) else None}
    desc = str(body["description"]).strip() if body.get("description") else ""
    lines = _schema_lines(spec, body.get("schema")) if body.get("schema") else []
    parts = [p for p in (desc, "\n".join(lines)) if p]
    return "**Request body:**\n\n" + "\n\n".join(parts) if parts else ""


def _responses(spec: dict[str, Any], op: dict[str, Any]) -> str:
    responses = op.get("responses")
    if not isinstance(responses, dict) or not responses:
        return ""
    out = ["**Responses:**"]
    for code, resp in responses.items():
        desc = str(_resolve_ref(spec, resp).get("description", "")).strip()
        if not desc and isinstance(resp, dict) and "$ref" in resp:
            name = str(resp["$ref"]).rsplit("/", 1)[-1]
            if name != str(code) and not name.isdigit() and name != "default":
                desc = name  # a descriptive ref name; skip code-named refs (e.g. DO's "401")
        out.append(f"- `{code}` — {desc}" if desc else f"- `{code}`")
    return "\n".join(out)


def _tags(spec: dict[str, Any]) -> str:
    sections = [
        f"### {tag['name']}\n\n{str(tag['description']).strip()}"
        for tag in _as_list(spec.get("tags"))
        if isinstance(tag, dict) and tag.get("name") and tag.get("description")
    ]
    return "\n\n".join(["## Tags", *sections]) if sections else ""


def _render_operation(
    spec: dict[str, Any],
    is_v2: bool,
    method: str,
    path: str,
    op: dict[str, Any],
    common: list[Any],
) -> str:
    parts = [f"## {method.upper()} {path}"]
    summary = str(op["summary"]).strip() if op.get("summary") else ""
    description = str(op["description"]).strip() if op.get("description") else ""
    if summary:
        parts.append(summary)
    if description and description != summary:
        parts.append(description)
    params = common + _as_list(op.get("parameters"))
    parts.append(_render_params(spec, params))
    parts.append(_request_body(spec, is_v2, op, params))
    parts.append(_responses(spec, op))
    return "\n\n".join(p for p in parts if p)
