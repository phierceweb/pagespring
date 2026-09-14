"""_openapi_render — ``$ref`` request bodies and path items, tag docs, malformed specs."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from typing import Any

from pagespring.patterns import _openapi_render


def _spec(**extra: Any) -> dict[str, Any]:
    return {"openapi": "3.1.0", "info": {"title": "T", "version": "1"}, **extra}


def test_request_body_ref_renders_its_schema():
    spec = _spec(
        paths={
            "/users": {
                "post": {
                    "requestBody": {"$ref": "#/components/requestBodies/NewUser"},
                    "responses": {"201": {"description": "Created"}},
                }
            }
        },
        components={
            "requestBodies": {
                "NewUser": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/User"}}
                    }
                }
            },
            "schemas": {
                "User": {
                    "type": "object",
                    "required": ["email"],
                    "properties": {"email": {"type": "string", "description": "Login address"}},
                }
            },
        },
    )
    md = _openapi_render.render(spec, "T 1")
    assert "**Request body:**" in md
    assert "- `email` _string_ (required) — Login address" in md


def test_request_body_description_is_rendered():
    spec = _spec(
        paths={
            "/x": {
                "put": {
                    "requestBody": {
                        "description": "The replacement record.",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                }
            }
        }
    )
    assert "The replacement record." in _openapi_render.render(spec, "T 1")


def test_path_item_ref_renders_and_counts_its_operations():
    spec = _spec(
        paths={"/pets": {"$ref": "#/components/pathItems/Pets"}},
        components={
            "pathItems": {
                "Pets": {
                    "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
                    "get": {"summary": "List pets"},
                    "delete": {"summary": "Remove all pets"},
                }
            }
        },
    )
    md = _openapi_render.render(spec, "T 1")
    assert "## GET /pets" in md and "List pets" in md
    assert "## DELETE /pets" in md
    assert "`limit`" in md
    assert _openapi_render.count_operations(spec) == 2


def test_ref_chain_is_followed_and_a_cycle_resolves_to_nothing():
    spec = _spec(
        paths={
            "/a": {"$ref": "#/components/pathItems/A"},
            "/loop": {"$ref": "#/components/pathItems/L1"},
        },
        components={
            "pathItems": {
                "A": {"$ref": "#/components/pathItems/B"},
                "B": {"get": {"summary": "Reached B"}},
                "L1": {"$ref": "#/components/pathItems/L2"},
                "L2": {"$ref": "#/components/pathItems/L1"},
            }
        },
    )
    md = _openapi_render.render(spec, "T 1")
    assert "## GET /a" in md and "Reached B" in md
    assert "/loop" not in md
    assert _openapi_render.count_operations(spec) == 1


def test_escaped_json_pointer_resolves():
    spec = _spec(
        paths={
            "/users/{id}": {"get": {"summary": "Get user"}},
            "/me": {"$ref": "#/paths/~1users~1{id}"},
            "/self": {"$ref": "#/paths/~1users~1%7Bid%7D"},
        }
    )
    md = _openapi_render.render(spec, "T 1")
    assert md.count("Get user") == 3


def test_external_ref_resolves_to_nothing_instead_of_the_whole_spec():
    spec = _spec(
        description="top-level text that must not leak",
        paths={"/x": {"get": {"responses": {"200": {"$ref": "responses.yaml"}}}}},
    )
    md = _openapi_render.render(spec, "T 1")
    assert "must not leak" not in md


def test_tag_descriptions_are_rendered():
    spec = _spec(
        tags=[
            {"name": "pets", "description": "Everything about your pets.\n\nSee the guide."},
            {"name": "bare"},
            "not-a-tag",
        ],
        paths={"/pets": {"get": {"summary": "List"}}},
    )
    md = _openapi_render.render(spec, "T 1")
    assert "## Tags" in md
    assert "### pets\n\nEverything about your pets.\n\nSee the guide." in md
    assert "### bare" not in md
    assert md.index("## Tags") < md.index("## GET /pets")


def test_no_described_tags_renders_no_tag_section():
    spec = _spec(tags=[{"name": "a"}], paths={"/a": {"get": {}}})
    assert "## Tags" not in _openapi_render.render(spec, "T 1")


def test_boolean_required_on_a_schema_does_not_crash():
    spec = _spec(
        paths={
            "/x": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": True,
                                    "properties": {"a": {"type": "string"}},
                                }
                            }
                        }
                    }
                }
            }
        }
    )
    md = _openapi_render.render(spec, "T 1")
    assert "- `a` _string_" in md
    assert "(required)" not in md


def test_servers_as_a_mapping_is_ignored():
    spec = _spec(servers={"url": "https://api.example"}, paths={"/x": {"get": {}}})
    md = _openapi_render.render(spec, "T 1")
    assert "## GET /x" in md
    assert "Base URL" not in md


def test_swagger_schemes_as_a_string_is_used_as_the_scheme():
    spec = {"swagger": "2.0", "host": "api.example", "basePath": "/v1", "schemes": "http"}
    assert "`http://api.example/v1`" in _openapi_render.render(spec, "T 1")


def test_type_list_renders_as_a_union():
    spec = _spec(
        paths={
            "/x": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"properties": {"n": {"type": ["string", "null"]}}}
                            }
                        }
                    }
                }
            }
        }
    )
    assert "- `n` _string | null_" in _openapi_render.render(spec, "T 1")


_SAMPLE_V3: dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {"title": "S", "version": "1"},
    "servers": [{"url": "https://s.example"}],
    "tags": [{"name": "t", "description": "td"}],
    "paths": {
        "/r": {"$ref": "#/components/pathItems/R"},
        "/p": {
            "put": {
                "requestBody": {"$ref": "#/components/requestBodies/RB"},
                "responses": {"default": {"$ref": "#/components/responses/Err"}},
            }
        },
    },
    "components": {
        "pathItems": {"R": {"get": {"summary": "r"}}},
        "requestBodies": {
            "RB": {
                "description": "rb",
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/S"}}},
            }
        },
        "schemas": {
            "S": {
                "type": "object",
                "required": ["a"],
                "properties": {"a": {"type": ["string", "null"], "description": "ad"}},
            }
        },
        "responses": {"Err": {"description": "error"}},
    },
}

_SAMPLE_V2: dict[str, Any] = {
    "swagger": "2.0",
    "info": {"title": "S", "version": "1", "description": "d"},
    "host": "h.example",
    "basePath": "/b",
    "schemes": ["https"],
    "tags": [{"name": "t", "description": "td"}],
    "paths": {
        "/p": {
            "parameters": [{"name": "c", "in": "query", "type": "string"}],
            "post": {
                "summary": "s",
                "description": "d",
                "parameters": [
                    {"name": "q", "in": "query", "schema": {"type": "string"}, "required": True},
                    {"in": "body", "name": "b", "schema": {"$ref": "#/definitions/B"}},
                ],
                "responses": {"200": {"description": "ok"}, "404": {"$ref": "#/r/NotFound"}},
            },
        }
    },
    "definitions": {
        "B": {"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}}
    },
}

_WRONG_TYPES: tuple[Any, ...] = (None, True, 0, "str", [], {}, [1], {"k": 1})


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


def test_no_wrong_typed_field_crashes_render_or_count():
    failures = []
    for base in (_SAMPLE_V2, _SAMPLE_V3):
        for path, bad in _variants(base):
            spec = _replaced(base, path, bad)
            try:
                _openapi_render.render(spec, "S 1")
                _openapi_render.count_operations(spec)
            except Exception as exc:  # noqa: BLE001 — collect every crash, not just the first
                failures.append(f"{path}={bad!r}: {type(exc).__name__}: {exc}")
    assert failures == []
