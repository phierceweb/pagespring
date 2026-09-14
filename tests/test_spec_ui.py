"""_spec_ui — API reference UI detection and spec discovery (fetch injected)."""

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.patterns import _spec_ui

_OPENAPI = (
    '{"openapi": "3.0.0", "info": {"title": "Engine API", "version": "1.56"}, '
    '"paths": {"/ping": {"get": {"summary": "Ping"}}}}'
)
_REDOC_ELEMENT = (
    "<html><body><redoc spec-url=/reference/api/engine/version/v1.56.yaml hide-hostname=true>"
    '</redoc><script src="https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js">'
    "</script></body></html>"
)
_REDOC_INIT = """<html><body><div id="api-doc"></div>
<script src="https://cdn.jsdelivr.net/npm/redoc@v2.5.1/bundles/redoc.standalone.js"></script>
<script>
  var spec = '/openapi/jellyfin-openapi-stable.json';
  const version = new URLSearchParams(window.location.search).get('version');
  if (version === 'unstable') {
    spec = '/openapi/jellyfin-openapi-unstable.json';
  } else if (version) {
    spec = `/openapi/stable/jellyfin-openapi-${version}.json`;
  }
  Redoc.init(spec, {}, document.getElementById('api-doc'));
</script></body></html>"""
_SCALAR_TWO_SOURCES = """<html><body><div id="app"></div>
<script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>
<script>
  Scalar.createApiReference('#app', {
    sources: [
      { title: 'V3', url: 'https://raw.example.com/api/v3/openapi.json' },
      { title: 'V5', url: 'https://raw.example.com/api/v5/openapi.json' },
    ],
  })
</script></body></html>"""
_SWAGGER_SHELL = (
    '<html><body><div id="swagger-ui"></div><script src="./swagger-ui-bundle.js"></script>'
    '<script src="./swagger-initializer.js"></script></body></html>'
)
_SWAGGER_INITIALIZER = (
    "window.onload = function() {\n"
    '  window.ui = SwaggerUIBundle({ url: "/v2/swagger.json", dom_id: "#swagger-ui" });\n'
    "};\n"
)
_SWAGGER_COMPUTED = """<html><body><div id="swagger-ui"></div>
<script src="./swagger-ui-bundle.js"></script>
<script>
  function loadSwagger(apiVersion) {
    window.ui = SwaggerUIBundle({ url: `${apiVersion}`, dom_id: '#swagger-ui' });
  }
</script></body></html>"""
_PROSE_ABOUT_SWAGGER_UI = (
    '<html><head><meta name="generator" content="Docusaurus v3.8.1"></head><body><article>'
    "<p>Serve the swagger-ui bundle, then call SwaggerUIBundle() or Redoc.init() yourself.</p>"
    "</article></body></html>"
)

_NEXT_PAYLOAD_ABOUT_SCALAR = (
    "<html><body><main><h1>API reference</h1></main>"
    "<script>self.__next_f.push([1,\"Call Scalar.createApiReference('#app', "
    "{ url: '/openapi.json' })\"])</script></body></html>"
)


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def _fetcher(bodies):
    requested = []

    def fetch(url):
        requested.append(url)
        return bodies.get(url)

    return fetch, requested


@pytest.mark.parametrize(
    ("html", "ui"),
    [
        (_REDOC_ELEMENT, "redoc"),
        (_REDOC_INIT, "redoc"),
        (_SCALAR_TWO_SOURCES, "scalar"),
        (_SWAGGER_SHELL, "swagger-ui"),
        (_PROSE_ABOUT_SWAGGER_UI, None),
        (_NEXT_PAYLOAD_ABOUT_SCALAR, None),
    ],
    ids=["redoc-element", "redoc-init", "scalar", "swagger-ui", "prose-mention", "next-payload"],
)
def test_ui_name_needs_the_ui_structure_not_its_name(html, ui):
    assert _spec_ui.ui_name(html) == ui


def test_a_redoc_spec_url_attribute_names_the_spec():
    page = "https://docs.docker.com/reference/api/engine/version/v1.56/"
    spec = "https://docs.docker.com/reference/api/engine/version/v1.56.yaml"
    fetch, requested = _fetcher({spec: _OPENAPI})

    assert _spec_ui.find_spec(page, _REDOC_ELEMENT, "redoc", fetch=fetch) == (spec, _OPENAPI)
    assert requested == [spec]


def test_swagger_ui_reads_its_initializer_script():
    page = "https://petstore.example/"
    spec = "https://petstore.example/v2/swagger.json"
    fetch, _ = _fetcher(
        {
            "https://petstore.example/swagger-initializer.js": _SWAGGER_INITIALIZER,
            spec: '{"swagger": "2.0", "info": {"title": "Petstore"}, "paths": {}}',
        }
    )

    assert _spec_ui.find_spec(page, _SWAGGER_SHELL, "swagger-ui", fetch=fetch)[0] == spec


def test_several_proven_specs_are_listed_not_chosen():
    v3 = "https://raw.example.com/api/v3/openapi.json"
    v5 = "https://raw.example.com/api/v5/openapi.json"
    fetch, _ = _fetcher({v3: _OPENAPI, v5: _OPENAPI})

    with pytest.raises(InvalidInputError) as exc:
        _spec_ui.find_spec(
            "https://sonarr.example/docs/api/", _SCALAR_TWO_SOURCES, "scalar", fetch=fetch
        )

    assert v3 in str(exc.value) and v5 in str(exc.value)


def test_a_candidate_that_is_not_openapi_is_discarded():
    stable = "https://api.jellyfin.example/openapi/jellyfin-openapi-stable.json"
    unstable = "https://api.jellyfin.example/openapi/jellyfin-openapi-unstable.json"
    fetch, requested = _fetcher({stable: _OPENAPI, unstable: "<html><body>404</body></html>"})

    found, _body = _spec_ui.find_spec(
        "https://api.jellyfin.example/", _REDOC_INIT, "redoc", fetch=fetch
    )

    assert found == stable
    assert requested == [stable, unstable]  # the ${version} template is not a candidate


def test_too_many_candidates_are_listed_without_fetching():
    urls = [f"https://svc{i}.example/openapi.json" for i in range(6)]
    script = "const services = [" + ", ".join(f'"{u}"' for u in urls) + "];"
    html = (
        '<html><body><div id="swagger-ui"></div><script src="./swagger-ui-bundle.js"></script>'
        f"<script>{script}</script></body></html>"
    )
    fetch, requested = _fetcher({})

    with pytest.raises(InvalidInputError, match="6 spec URLs"):
        _spec_ui.find_spec("https://petstore.example/", html, "swagger-ui", fetch=fetch)

    assert requested == []


def test_a_ui_whose_spec_url_is_computed_is_refused_by_name():
    fetch, requested = _fetcher({})

    with pytest.raises(InvalidInputError, match="swagger-ui page"):
        _spec_ui.find_spec(
            "https://radarr.example/docs/api/", _SWAGGER_COMPUTED, "swagger-ui", fetch=fetch
        )

    assert requested == []


@pytest.mark.parametrize(
    ("page", "config", "expected"),
    [
        (
            "https://api.example.com/swagger/index.html",
            'url: "v1/swagger.json"',
            "https://api.example.com/swagger/v1/swagger.json",
        ),
        (
            "https://api.example.com/docs/",
            "url: 'https://api.example.com:8443/openapi.yaml'",
            "https://api.example.com:8443/openapi.yaml",
        ),
        (
            "https://api.example.com/docs/",
            "url: 'https://cdn.jsdelivr.net/npm/@acme/api@2.1.0/openapi.json'",
            "https://cdn.jsdelivr.net/npm/@acme/api@2.1.0/openapi.json",
        ),
        (
            "https://api.example.com/docs/",
            'url: "/api/openapi.json?version=2"',
            "https://api.example.com/api/openapi.json?version=2",
        ),
    ],
    ids=["relative-without-slash", "port", "npm-scope", "query"],
)
def test_a_quoted_spec_url_is_taken_whole(page, config, expected):
    html = (
        '<html><body><div id="swagger-ui"></div><script src="./swagger-ui-bundle.js"></script>'
        f"<script>SwaggerUIBundle({{ {config}, dom_id: '#swagger-ui' }})</script></body></html>"
    )
    fetch, requested = _fetcher({expected: _OPENAPI})

    assert _spec_ui.find_spec(page, html, "swagger-ui", fetch=fetch) == (expected, _OPENAPI)
    assert requested == [expected]


_STOCK_INITIALIZER = (
    "window.onload = function() {\n"
    "  window.ui = SwaggerUIBundle({\n"
    '    url: "https://petstore.swagger.io/v2/swagger.json",\n'
    "    dom_id: '#swagger-ui',\n"
    "  });\n"
    "};\n"
)
_SPRINGDOC_INITIALIZER = (
    "window.onload = function() {\n"
    "  window.ui = SwaggerUIBundle({\n"
    '    url: "https://petstore.swagger.io/v2/swagger.json",\n'
    "    dom_id: '#swagger-ui',\n"
    '    "configUrl" : "/v3/api-docs/swagger-config",\n'
    '    "validatorUrl" : ""\n'
    "  });\n"
    "};\n"
)
_PETSTORE = '{"swagger": "2.0", "info": {"title": "Swagger Petstore"}, "paths": {}}'


def test_the_page_urls_url_query_names_the_spec_over_the_stock_initializer():
    page = "https://api.example.com/swagger/index.html?url=/api/openapi.json"
    spec = "https://api.example.com/api/openapi.json"
    fetch, requested = _fetcher(
        {
            "https://api.example.com/swagger/swagger-initializer.js": _STOCK_INITIALIZER,
            "https://petstore.swagger.io/v2/swagger.json": _PETSTORE,
            spec: _OPENAPI,
        }
    )

    assert _spec_ui.find_spec(page, _SWAGGER_SHELL, "swagger-ui", fetch=fetch) == (spec, _OPENAPI)
    assert requested == [spec]


def test_the_page_urls_url_query_picks_one_of_the_specs_a_config_lists():
    page = "https://api.example.com/swagger-ui/index.html?url=/v3/api-docs/admin"
    admin = "https://api.example.com/v3/api-docs/admin"
    fetch, requested = _fetcher(
        {
            "https://api.example.com/swagger-ui/swagger-initializer.js": _SPRINGDOC_INITIALIZER,
            "https://api.example.com/v3/api-docs/swagger-config": (
                '{"urls": [{"url": "/v3/api-docs/public"}, {"url": "/v3/api-docs/admin"}]}'
            ),
            "https://api.example.com/v3/api-docs/public": _OPENAPI,
            admin: _OPENAPI,
        }
    )

    assert _spec_ui.find_spec(page, _SWAGGER_SHELL, "swagger-ui", fetch=fetch) == (admin, _OPENAPI)
    assert requested == [admin]


@pytest.mark.parametrize(
    ("page", "initializer"),
    [
        ("https://api.example.com/swagger-ui/index.html", _SPRINGDOC_INITIALIZER),
        (
            "https://api.example.com/swagger-ui/index.html?configUrl=/v3/api-docs/swagger-config",
            _STOCK_INITIALIZER,
        ),
    ],
    ids=["config-url-in-initializer", "config-url-query"],
)
def test_a_config_url_names_the_spec_its_config_document_lists(page, initializer):
    spec = "https://api.example.com/v3/api-docs"
    fetch, requested = _fetcher(
        {
            "https://api.example.com/swagger-ui/swagger-initializer.js": initializer,
            "https://api.example.com/v3/api-docs/swagger-config": (
                '{"configUrl": "/v3/api-docs/swagger-config", "url": "/v3/api-docs"}'
            ),
            "https://petstore.swagger.io/v2/swagger.json": _PETSTORE,
            spec: _OPENAPI,
        }
    )

    assert _spec_ui.find_spec(page, _SWAGGER_SHELL, "swagger-ui", fetch=fetch) == (spec, _OPENAPI)
    assert "https://petstore.swagger.io/v2/swagger.json" not in requested


def test_every_spec_a_config_document_lists_is_a_candidate():
    page = "https://api.example.com/swagger-ui/index.html"
    public = "https://api.example.com/v3/api-docs/public"
    admin = "https://api.example.com/v3/api-docs/admin"
    fetch, _ = _fetcher(
        {
            "https://api.example.com/swagger-ui/swagger-initializer.js": _SPRINGDOC_INITIALIZER,
            "https://api.example.com/v3/api-docs/swagger-config": (
                '{"urls": [{"url": "/v3/api-docs/public", "name": "public"},'
                ' {"url": "/v3/api-docs/admin", "name": "admin"}]}'
            ),
            public: _OPENAPI,
            admin: _OPENAPI,
        }
    )

    with pytest.raises(InvalidInputError, match="serves 2 spec URLs") as exc:
        _spec_ui.find_spec(page, _SWAGGER_SHELL, "swagger-ui", fetch=fetch)

    assert public in str(exc.value) and admin in str(exc.value)


@pytest.mark.parametrize(
    "demo",
    [
        "https://petstore.swagger.io/v2/swagger.json",
        "https://petstore3.swagger.io/api/v3/openapi.json",
    ],
    ids=["petstore", "petstore3"],
)
def test_swaggers_demo_spec_is_never_a_candidate(demo):
    fetch, requested = _fetcher(
        {
            "https://api.example.com/swagger/swagger-initializer.js": _STOCK_INITIALIZER.replace(
                "https://petstore.swagger.io/v2/swagger.json", demo
            ),
            demo: _PETSTORE,
        }
    )

    with pytest.raises(InvalidInputError, match="swagger-ui page"):
        _spec_ui.find_spec(
            "https://api.example.com/swagger/index.html", _SWAGGER_SHELL, "swagger-ui", fetch=fetch
        )

    assert demo not in requested


def test_every_fetch_is_paced_behind_the_page_fetch(monkeypatch):
    page = "https://api.example.com/swagger-ui/index.html"
    events = []
    bodies = {
        "https://api.example.com/swagger-ui/swagger-initializer.js": _SPRINGDOC_INITIALIZER,
        "https://api.example.com/v3/api-docs/swagger-config": '{"url": "/v3/api-docs"}',
        "https://api.example.com/v3/api-docs": _OPENAPI,
    }

    def fetch(url):
        events.append(url)
        return bodies.get(url)

    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: events.append("sleep"))

    _spec_ui.find_spec(page, _SWAGGER_SHELL, "swagger-ui", fetch=fetch)

    assert events == [
        "sleep",
        "https://api.example.com/swagger-ui/swagger-initializer.js",
        "sleep",
        "https://api.example.com/v3/api-docs/swagger-config",
        "sleep",
        "https://api.example.com/v3/api-docs",
    ]


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ("url: `/api/openapi.json?v=${Date.now()}`", "https://api.example.com/api/openapi.json"),
        ('url: "/api/openapi.json?v={{version}}"', "https://api.example.com/api/openapi.json"),
        (
            'JSON.parse("{\\"url\\":\\"/api/openapi.json?group=public\\"}")',
            "https://api.example.com/api/openapi.json?group=public",
        ),
    ],
    ids=["interpolated-query", "templated-query", "json-escaped"],
)
def test_a_spec_url_with_an_interpolated_or_escaped_query_is_still_found(config, expected):
    html = (
        '<html><body><div id="swagger-ui"></div><script src="./swagger-ui-bundle.js"></script>'
        f"<script>SwaggerUIBundle({{ {config}, dom_id: '#swagger-ui' }})</script></body></html>"
    )
    fetch, requested = _fetcher({expected: _OPENAPI})

    assert (
        _spec_ui.find_spec("https://api.example.com/docs/", html, "swagger-ui", fetch=fetch)[0]
        == expected
    )
    assert requested == [expected]
