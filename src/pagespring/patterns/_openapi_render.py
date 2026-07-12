"""Render an OpenAPI 3.x / Swagger 2.0 spec to clean markdown — one section per
operation, with params, request body, responses, and resolved ``$ref`` schemas.

Pure transformation over a parsed dict; no network, no file I/O.
"""

from __future__ import annotations

from typing import Any

_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")


def count_operations(spec: dict[str, Any]) -> int:
    """Number of HTTP operations across all paths."""
    n = 0
    for item in spec.get("paths", {}).values():
        if isinstance(item, dict):
            n += sum(1 for m in _METHODS if isinstance(item.get(m), dict))
    return n


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

    for path, item in spec.get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        common = item.get("parameters", []) if isinstance(item.get("parameters"), list) else []
        for method in _METHODS:
            op = item.get(method)
            if isinstance(op, dict):
                out.append(_render_operation(spec, is_v2, method, str(path), op, common))
    return "\n\n".join(out) + "\n"


def _base_url(spec: dict[str, Any], is_v2: bool) -> str | None:
    if is_v2:
        host = spec.get("host", "")
        base = spec.get("basePath", "")
        schemes = spec.get("schemes") or ["https"]
        return f"{schemes[0]}://{host}{base}" if host else (base or None)
    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict):
        return servers[0].get("url")
    return None


def _resolve_ref(spec: dict[str, Any], node: Any) -> dict[str, Any]:
    """Follow a single ``{"$ref": "#/a/b/c"}`` one level into the spec."""
    if isinstance(node, dict) and "$ref" in node:
        target: Any = spec
        for part in str(node["$ref"]).split("/")[1:]:  # drop leading '#'
            target = target.get(part, {}) if isinstance(target, dict) else {}
        return target if isinstance(target, dict) else {}
    return node if isinstance(node, dict) else {}


def _schema_lines(spec: dict[str, Any], schema: Any) -> list[str]:
    """One bullet per property of a (possibly ``$ref``'d) object schema."""
    schema = _resolve_ref(spec, schema)
    props = schema.get("properties")
    if not isinstance(props, dict):
        t = schema.get("type")
        return [f"- _{t}_"] if t else []
    required = set(schema.get("required", []))
    lines: list[str] = []
    for name, prop in props.items():
        prop = _resolve_ref(spec, prop)
        typ = prop.get("type", "object")
        req = " (required)" if name in required else ""
        desc = f" — {str(prop['description'])}" if prop.get("description") else ""
        lines.append(f"- `{name}` _{typ}_{req}{desc}")
    return lines


def _render_params(spec: dict[str, Any], params: list[Any]) -> str:
    rows: list[str] = []
    for p in params:
        p = _resolve_ref(spec, p)
        if p.get("in") == "body":  # v2 body param renders as a request body
            continue
        if not p.get("name"):
            continue  # unresolved/nameless param → skip the empty row
        typ = (p.get("schema") or {}).get("type") or p.get("type") or ""
        req = "yes" if p.get("required") else "no"
        desc = str(p.get("description") or "").replace("\n", " ")
        rows.append(f"| `{p.get('name', '')}` | {p.get('in', '')} | {typ} | {req} | {desc} |")
    if not rows:
        return ""
    head = "| Name | In | Type | Required | Description |\n| --- | --- | --- | --- | --- |"
    return "**Parameters:**\n\n" + head + "\n" + "\n".join(rows)


def _request_body(spec: dict[str, Any], is_v2: bool, op: dict[str, Any], params: list[Any]) -> str:
    if is_v2:
        body = next((p for p in params if _resolve_ref(spec, p).get("in") == "body"), None)
        schema = _resolve_ref(spec, body).get("schema") if body else None
    else:
        rb = op.get("requestBody", {})
        content = rb.get("content", {}) if isinstance(rb, dict) else {}
        first: dict[str, Any] = (
            next(iter(content.values()), {}) if isinstance(content, dict) else {}
        )
        schema = first.get("schema") if isinstance(first, dict) else None
    if not schema:
        return ""
    lines = _schema_lines(spec, schema)
    return "**Request body:**\n\n" + "\n".join(lines) if lines else ""


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
    op_params = op.get("parameters")
    op_params = op_params if isinstance(op_params, list) else []
    params = list(common) + op_params
    parts.append(_render_params(spec, params))
    parts.append(_request_body(spec, is_v2, op, params))
    parts.append(_responses(spec, op))
    return "\n\n".join(p for p in parts if p)
