"""_postman_render — descriptions, query/path variables, every body mode, malformed input."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from typing import Any

from pagespring.patterns import _postman_render


def _coll(*items: dict[str, Any], **info: Any) -> dict[str, Any]:
    return {"info": {"name": "C", **info}, "item": list(items)}


def _request(**req: Any) -> dict[str, Any]:
    return {"name": "Req", "request": {"method": "POST", "url": "https://api.example/x", **req}}


def test_folder_description_is_rendered_under_its_heading():
    folder = {"name": "Users", "description": "Manage user accounts.", "item": [_request()]}
    md = _postman_render.render(_coll(folder), "C")
    assert "## Users\n\nManage user accounts.\n\n### Req" in md


def test_request_and_item_descriptions_are_rendered():
    item = _request(description={"content": "Creates a thing.", "type": "text/markdown"})
    item["description"] = "Item-level note."
    md = _postman_render.render(_coll(item), "C")
    assert "Creates a thing." in md
    assert "Item-level note." in md
    assert md.index("`POST https://api.example/x`") < md.index("Creates a thing.")


def test_query_and_path_variables_render_with_descriptions():
    url = {
        "raw": "https://api.example/users/:id?limit=10",
        "query": [
            {"key": "limit", "value": "10", "description": "Page size"},
            {"key": "cursor", "value": "", "disabled": True, "description": "Next page token"},
        ],
        "variable": [{"key": "id", "value": "42", "description": {"content": "User id"}}],
    }
    md = _postman_render.render(_coll(_request(url=url)), "C")
    assert "**Query parameters:**\n\n- `limit=10` — Page size" in md
    assert "- `cursor` (disabled) — Next page token" in md
    assert "**Path variables:**\n\n- `id: 42` — User id" in md


def test_header_description_is_rendered():
    header = [{"key": "X-Api-Key", "value": "{{key}}", "description": "Your API key"}]
    md = _postman_render.render(_coll(_request(header=header)), "C")
    assert "- `X-Api-Key: {{key}}` — Your API key" in md


def test_raw_body_uses_its_language_as_the_fence():
    body = {"mode": "raw", "raw": '{"a": 1}', "options": {"raw": {"language": "json"}}}
    md = _postman_render.render(_coll(_request(body=body)), "C")
    assert '**Body:**\n\n```json\n{"a": 1}\n```' in md


def test_urlencoded_body_renders_fields():
    body = {
        "mode": "urlencoded",
        "urlencoded": [{"key": "grant_type", "value": "client_credentials", "description": "Flow"}],
    }
    md = _postman_render.render(_coll(_request(body=body)), "C")
    assert "**Body (urlencoded):**\n\n- `grant_type=client_credentials` — Flow" in md


def test_formdata_body_renders_text_and_file_fields():
    body = {
        "mode": "formdata",
        "formdata": [
            {"key": "title", "value": "Report", "type": "text", "description": "Doc title"},
            {"key": "upload", "type": "file", "src": "/tmp/report.pdf", "description": "The file"},
        ],
    }
    md = _postman_render.render(_coll(_request(body=body)), "C")
    assert "**Body (form-data):**" in md
    assert "- `title=Report` — Doc title" in md
    assert "- `upload` (file: /tmp/report.pdf) — The file" in md


def test_graphql_body_renders_query_and_variables():
    body = {
        "mode": "graphql",
        "graphql": {"query": "query { me { id } }", "variables": '{"x": 1}'},
    }
    md = _postman_render.render(_coll(_request(body=body)), "C")
    assert "**Body (GraphQL):**\n\n```graphql\nquery { me { id } }\n```" in md
    assert '```json\n{"x": 1}\n```' in md


def test_file_body_renders_its_source():
    body = {"mode": "file", "file": {"src": "payload.bin"}}
    md = _postman_render.render(_coll(_request(body=body)), "C")
    assert "**Body (file):** `payload.bin`" in md


def test_only_the_active_body_mode_renders():
    body = {"mode": "urlencoded", "raw": "stale raw", "urlencoded": [{"key": "a", "value": "1"}]}
    md = _postman_render.render(_coll(_request(body=body)), "C")
    assert "stale raw" not in md
    assert "- `a=1`" in md


def test_a_string_request_renders_as_a_get():
    md = _postman_render.render(_coll({"name": "Ping", "request": "https://api.example/ping"}), "C")
    assert "`GET https://api.example/ping`" in md


_SAMPLE: dict[str, Any] = {
    "info": {"name": "S", "description": {"content": "top", "type": "text/markdown"}},
    "item": [
        {
            "name": "F",
            "description": "fd",
            "item": [
                {
                    "name": "R",
                    "description": "idesc",
                    "request": {
                        "method": "POST",
                        "description": "rdesc",
                        "url": {
                            "raw": "https://a.example/:id?q=1",
                            "query": [{"key": "q", "value": "1", "description": "qd"}],
                            "variable": [{"key": "id", "value": "2", "description": "vd"}],
                        },
                        "header": [{"key": "H", "value": "v", "description": "hd"}],
                        "body": {
                            "mode": "formdata",
                            "formdata": [{"key": "f", "type": "file", "src": ["a", "b"]}],
                            "graphql": {"query": "{ a }", "variables": "{}"},
                            "file": {"src": "x"},
                            "urlencoded": [{"key": "u", "value": "1"}],
                            "options": {"raw": {"language": "json"}},
                        },
                    },
                    "response": [{"name": "ok", "code": 200}],
                }
            ],
        }
    ],
}

_WRONG_TYPES: tuple[Any, ...] = (None, True, 0, "str", [], {}, [1], {"k": 1})
_MODES = ("raw", "urlencoded", "formdata", "graphql", "file", None, 5)


def _variants(node: Any, path: tuple[Any, ...] = ()) -> Iterator[tuple[tuple[Any, ...], Any]]:
    """Every node position in ``node``, each replaced by every wrong-typed value."""
    children = node.items() if isinstance(node, dict) else enumerate(node)
    for key, child in children:
        for bad in _WRONG_TYPES:
            yield (*path, key), bad
        if isinstance(child, (dict, list)):
            yield from _variants(child, (*path, key))


def _replaced(doc: Any, path: tuple[Any, ...], value: Any) -> Any:
    out = copy.deepcopy(doc)
    target = out
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return out


def test_no_wrong_typed_field_crashes_render():
    body_path = ("item", 0, "item", 0, "request", "body", "mode")
    failures = []
    for mode in _MODES:
        base = _replaced(_SAMPLE, body_path, mode)
        for path, bad in _variants(base):
            coll = _replaced(base, path, bad)
            try:
                _postman_render.render(coll, "S")
                _postman_render.count_requests(coll)
            except Exception as exc:  # noqa: BLE001 — collect every crash, not just the first
                failures.append(f"mode={mode!r} {path}={bad!r}: {type(exc).__name__}: {exc}")
    assert failures == []
