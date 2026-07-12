"""api_spec — match/sniff/render for OpenAPI & Postman specs (mocked fetch)."""

import json  # noqa: F401

import pytest  # noqa: F401

from pagespring import http  # noqa: F401
from pagespring.patterns.api_spec import ApiSpecPattern
from pagespring.registry import classify


def test_match_routes_spec_urls_and_paths():
    p = ApiSpecPattern()
    assert p.match("https://api.x.com/v1/openapi.json")
    assert p.match("https://api.x.com/swagger.yaml")
    assert p.match("https://api.x.com/spec.yml")
    assert p.match("./vendor-postman.json")
    assert p.match("https://x.com/docs/My-OpenAPI-Definition")  # token in last segment
    # Not specs:
    assert not p.match("https://docs.vendor.com/getting-started")
    assert not p.match("https://x.com/guide/intro.html")


def test_classify_routes_json_before_gitbook():
    # A spec on a docs.* host must reach api_spec, not the broad gitbook matcher.
    assert classify("https://docs.vendor.com/openapi.json").name == "api_spec"
    assert classify("https://api.x.com/swagger.yaml").name == "api_spec"


from pagespring.patterns import api_spec as mod  # noqa: E402


def test_load_data_parses_json_and_yaml():
    assert mod._load_data('{"a": 1}') == {"a": 1}
    assert mod._load_data("a: 1\nb: two\n") == {"a": 1, "b": "two"}


def test_sniff_format():
    assert mod.sniff_format({"openapi": "3.0.0", "paths": {}}) == "openapi"
    assert mod.sniff_format({"swagger": "2.0", "paths": {}}) == "openapi"
    assert mod.sniff_format({"info": {"_postman_id": "x", "schema": "…"}, "item": []}) == "postman"
    assert mod.sniff_format({"hello": "world"}) is None


def test_title_slug_from_info():
    title, slug = mod._title_slug(
        {"info": {"title": "ValidiFI API", "version": "4.0"}}, "openapi", "https://x/o.json"
    )
    assert title == "ValidiFI API 4.0"
    assert slug == "validifi-api-4-0"


def test_load_raw_local_file(tmp_path):
    f = tmp_path / "spec.json"
    f.write_text('{"openapi": "3.0.0"}', encoding="utf-8")
    assert mod._load_raw(str(f)) == '{"openapi": "3.0.0"}'


def test_load_raw_missing_file_is_clean_error():
    from pf_core.exceptions import InvalidInputError

    with pytest.raises(InvalidInputError):
        mod._load_raw("./does-not-exist.json")


from pagespring.patterns import _openapi_render  # noqa: E402

OPENAPI_3 = {
    "openapi": "3.0.3",
    "info": {"title": "Pet API", "version": "1.2.0", "description": "Manage pets."},
    "servers": [{"url": "https://api.pets.example/v1"}],
    "paths": {
        "/pets": {
            "get": {
                "summary": "List pets",
                "parameters": [
                    {
                        "name": "limit",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "integer"},
                        "description": "Max items",
                    }
                ],
                "responses": {"200": {"description": "A list of pets"}},
            },
            "post": {
                "summary": "Create a pet",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}
                    }
                },
                "responses": {"201": {"description": "Created"}},
            },
        }
    },
    "components": {
        "schemas": {
            "Pet": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "description": "Pet name"},
                    "tag": {"type": "string"},
                },
            }
        }
    },
}

SWAGGER_2 = {
    "swagger": "2.0",
    "info": {"title": "Legacy API", "version": "1.0"},
    "host": "api.legacy.example",
    "basePath": "/v1",
    "schemes": ["https"],
    "paths": {
        "/users": {
            "get": {
                "summary": "List users",
                "parameters": [
                    {"name": "page", "in": "query", "type": "integer", "required": False}
                ],
                "responses": {"200": {"description": "ok"}},
            }
        }
    },
}


def test_openapi_render_v3():
    md = _openapi_render.render(OPENAPI_3, "Pet API 1.2.0")
    assert md.startswith("# Pet API 1.2.0")
    assert "https://api.pets.example/v1" in md
    assert "## GET /pets" in md
    assert "`limit`" in md
    assert "## POST /pets" in md
    assert "`name`" in md and "(required)" in md  # resolved $ref schema props
    assert "`201`" in md
    assert _openapi_render.count_operations(OPENAPI_3) == 2


def test_openapi_render_v2():
    md = _openapi_render.render(SWAGGER_2, "Legacy API 1.0")
    assert "# Legacy API 1.0" in md
    assert "https://api.legacy.example/v1" in md  # host + basePath + scheme
    assert "## GET /users" in md
    assert "`page`" in md
    assert _openapi_render.count_operations(SWAGGER_2) == 1


def test_openapi_render_tolerates_null_parameters():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Edgy", "version": "1.0"},
        "paths": {"/x": {"get": {"parameters": None, "responses": {"200": {"description": "ok"}}}}},
    }
    md = _openapi_render.render(spec, "Edgy 1.0")
    assert "## GET /x" in md
    assert _openapi_render.count_operations(spec) == 1


from pagespring.patterns import _postman_render  # noqa: E402

POSTMAN = {
    "info": {
        "_postman_id": "abc-123",
        "name": "Acme Messaging",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
    },
    "item": [
        {
            "name": "Messaging",
            "item": [
                {
                    "name": "Send Message",
                    "request": {
                        "method": "POST",
                        "url": {"raw": "https://api.acme.example/v1/message/send"},
                        "header": [{"key": "Authorization", "value": "Bearer {{token}}"}],
                        "body": {"mode": "raw", "raw": '{"to":"+15550000000"}'},
                    },
                    "response": [{"name": "200 OK", "code": 200}],
                }
            ],
        }
    ],
}


def test_postman_render():
    md = _postman_render.render(POSTMAN, "Acme Messaging")
    assert md.startswith("# Acme Messaging")
    assert "## Messaging" in md  # folder heading (depth 2)
    assert "### Send Message" in md  # request heading (depth 3)
    assert "POST https://api.acme.example/v1/message/send" in md
    assert "Authorization" in md
    assert '{"to":"+15550000000"}' in md
    assert _postman_render.count_requests(POSTMAN) == 1


from pf_core.exceptions import InvalidInputError  # noqa: E402


def _fake_fetch(payload: dict):
    def _f(url, **kwargs):
        return url, json.dumps(payload)

    return _f


def test_acquire_normalize_openapi(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "fetch_text", _fake_fetch(OPENAPI_3))
    p = ApiSpecPattern()
    acq = p.acquire("https://api.pets.example/openapi.json", tmp_path)
    assert acq.kind == "markdown"
    assert acq.slug == "pet-api-1-2-0"
    assert acq.pages == 2
    md = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "# Pet API 1.2.0" in md
    assert "## GET /pets" in md and "## POST /pets" in md


def test_acquire_normalize_postman(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "fetch_text", _fake_fetch(POSTMAN))
    p = ApiSpecPattern()
    acq = p.acquire("https://x.com/acme-postman.json", tmp_path)
    assert acq.kind == "markdown"
    assert acq.pages == 1
    assert acq.slug == "acme-messaging"  # from info.name, not the URL filename
    md = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "### Send Message" in md


def test_local_file_ingest(tmp_path):
    f = tmp_path / "spec.json"
    f.write_text(json.dumps(OPENAPI_3), encoding="utf-8")
    p = ApiSpecPattern()
    acq = p.acquire(str(f), tmp_path)
    md = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "# Pet API 1.2.0" in md


def test_unrecognized_content_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "fetch_text", _fake_fetch({"hello": "world"}))
    with pytest.raises(InvalidInputError):
        ApiSpecPattern().acquire("https://x.com/thing.json", tmp_path)


def test_openapi_response_ref_falls_back_to_name():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "R", "version": "1"},
        "paths": {
            "/x": {
                "get": {
                    "responses": {
                        "401": {"$ref": "#/components/responses/unauthorized"},
                        "200": {"description": "ok"},
                    }
                }
            }
        },
        "components": {"responses": {"unauthorized": {"content": {}}}},  # no description
    }
    md = _openapi_render.render(spec, "R 1")
    assert "- `401` — unauthorized" in md  # fell back to the ref name
    assert "- `200` — ok" in md
    assert "- `401` — \n" not in md and "- `401` —  " not in md  # no dangling em-dash


def test_openapi_response_no_description_omits_dash():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "R", "version": "1"},
        "paths": {"/x": {"get": {"responses": {"204": {"description": ""}}}}},
    }
    md = _openapi_render.render(spec, "R 1")
    assert "- `204`" in md
    assert "- `204` —" not in md  # no trailing em-dash when there's no description


def test_openapi_skips_nameless_params():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "R", "version": "1"},
        "paths": {
            "/x": {
                "get": {
                    "parameters": [
                        {"$ref": "#/components/parameters/Missing"},  # resolves to {} → nameless
                        {"name": "real", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    md = _openapi_render.render(spec, "R 1")
    assert "`real`" in md
    assert "| `` |" not in md  # no empty-name row


def test_openapi_dedupes_summary_and_description():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "R", "version": "1"},
        "paths": {"/x": {"get": {"summary": "Do it", "description": "Do it", "responses": {}}}},
    }
    md = _openapi_render.render(spec, "R 1")
    assert md.count("Do it") == 1  # rendered once, not twice


def test_openapi_response_code_named_ref_no_redundancy():
    # DigitalOcean names response components by status code (#/.../responses/401),
    # so the ref-name fallback must NOT echo a redundant "401 — 401".
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "R", "version": "1"},
        "paths": {
            "/x": {
                "get": {
                    "responses": {
                        "401": {"$ref": "#/components/responses/401"},
                        "default": {"$ref": "#/components/responses/default"},
                    }
                }
            }
        },
        "components": {"responses": {"401": {"content": {}}, "default": {"content": {}}}},
    }
    md = _openapi_render.render(spec, "R 1")
    assert "- `401`" in md
    assert "- `401` — 401" not in md  # no redundant status-code echo
    assert "- `default`" in md
    assert "- `default` — default" not in md


def test_title_slug_uses_postman_info_name():
    # Postman collections name themselves via info.name, not info.title.
    title, slug = mod._title_slug(
        {"info": {"name": "Auth0 Management API v2"}}, "postman", "https://x/c.json"
    )
    assert title == "Auth0 Management API v2"
    assert slug == "auth0-management-api-v2"


def test_load_data_rejects_non_object():
    # A YAML sequence (or any non-mapping) is not a spec.
    with pytest.raises(InvalidInputError):
        mod._load_data("- a\n- b\n")


def test_normalize_rejects_unrecognized_raw(tmp_path):
    # normalize re-sniffs and must raise (not silently render as OpenAPI) if the
    # raw file isn't a recognizable spec.
    from pagespring.base import AcquireResult

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "spec.json").write_text('{"hello": "world"}', encoding="utf-8")
    acq = AcquireResult(raw_dir=raw_dir, kind="markdown", slug="x", pages=0, title=None)
    with pytest.raises(InvalidInputError):
        ApiSpecPattern().normalize(acq, tmp_path)
