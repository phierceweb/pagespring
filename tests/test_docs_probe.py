"""docs_probe — generator sniffing + strategy dispatch, all http mocked."""

import pytest
from pf_core.exceptions import ClientError, InvalidInputError

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns import _docusaurus, _hugo, _mkdocs, _sphinx, docs_probe, gitbook
from pagespring.patterns.docs_probe import DocsProbePattern

_MKDOCS_HOME = (
    '<html><head><meta name="generator" content="mkdocs-1.6.1"><title>M</title></head></html>'
)
_DOCUSAURUS_HOME = (
    '<html><head><meta name="generator" content="Docusaurus v3.8.1"><title>D</title></head></html>'
)
_SPHINX_HOME = (
    "<html><head><title>S</title>"
    '<script src="_static/documentation_options.js"></script></head><body></body></html>'
)
# A generic asset dir that happens to be named _static/ — not Sphinx.
_STATIC_ONLY_HOME = (
    '<html><head><title>X</title><link href="/assets/_static/theme.css"></head>'
    "<body><main>hi</main></body></html>"
)
_PLAIN_HOME = "<html><head><title>plain</title></head><body>nothing here</body></html>"


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def _fake_acquire(kind):
    def fake(base_url, workdir, *, slug, title):
        raw = workdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        return AcquireResult(raw_dir=raw, kind=kind, slug=slug, pages=1, title=title)

    return fake


def test_match_claims_only_web_urls():
    p = DocsProbePattern()
    assert p.match("https://anything.example.com/some/docs")
    assert p.match("http://ex.com")
    assert not p.match("./local-openapi.json")
    assert not p.match("file:///tmp/x.html")


@pytest.mark.parametrize(
    ("home", "strategy_mod", "strategy_name"),
    [
        (_MKDOCS_HOME, _mkdocs, "mkdocs"),
        (_DOCUSAURUS_HOME, _docusaurus, "docusaurus"),
        (_SPHINX_HOME, _sphinx, "sphinx"),
    ],
)
def test_probe_dispatches_by_generator(tmp_path, monkeypatch, home, strategy_mod, strategy_name):
    monkeypatch.setattr(http, "fetch_text", lambda url, **k: (url, home))
    called = {}

    def spy(base_url, workdir, *, slug, title):
        called["strategy"] = strategy_name
        called["slug"] = slug
        return _fake_acquire("html")(base_url, workdir, slug=slug, title=title)

    monkeypatch.setattr(strategy_mod, "acquire", spy)
    acq = DocsProbePattern().acquire("https://docs.ex.org", tmp_path)
    assert called["strategy"] == strategy_name
    assert called["slug"] == "ex"
    assert acq.slug == "ex"


def test_probe_unrecognized_raises_invalid_input(tmp_path, monkeypatch):
    def fake_fetch(url, **kwargs):
        if url.endswith(("search/search_index.json", "llms.txt")):
            raise RuntimeError("404")
        return url, _PLAIN_HOME

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    with pytest.raises(InvalidInputError) as exc_info:
        DocsProbePattern().acquire("https://plain.example.com", tmp_path)
    assert "probed" in str(exc_info.value)


def test_generic_static_dir_does_not_route_to_sphinx(tmp_path, monkeypatch):
    """A bare "_static/" anywhere in the body used to claim the site for Sphinx,
    whose extractor ends at <main> — so the mis-route succeeded silently."""

    def fake_fetch(url, **kwargs):
        if url.endswith(("search/search_index.json", "llms.txt")):
            raise RuntimeError("404")
        return url, _STATIC_ONLY_HOME

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    with pytest.raises(InvalidInputError):
        DocsProbePattern().acquire("https://docs.ex.org", tmp_path)


def test_normalize_markdown_concats_and_strips_banner(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "0000-a.md").write_text(
        "> For the complete documentation index, see [llms.txt](https://x/llms.txt). More.\n\n# A\n\nBody A.\n",
        encoding="utf-8",
    )
    (raw / "0001-b.md").write_text("# B\n\nBody B.\n", encoding="utf-8")
    acq = AcquireResult(raw_dir=raw, kind="markdown", slug="x", pages=2)
    out = DocsProbePattern().normalize(acq, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert out.name == "x.md"
    assert "For the complete documentation index" not in text
    assert text.index("# A") < text.index("# B")


def test_normalize_html_wraps_fragments_with_title(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "0000-a.html").write_text("<section><h1>A</h1></section>", encoding="utf-8")
    (raw / "0001-b.html").write_text("<section><h1>B</h1></section>", encoding="utf-8")
    acq = AcquireResult(raw_dir=raw, kind="html", slug="x", pages=2, title="X Manual")
    out = DocsProbePattern().normalize(acq, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert out.name == "x.html"
    assert "<title>X Manual</title>" in text
    assert text.index("<h1>A</h1>") < text.index("<h1>B</h1>")


def test_normalize_html_empty_raw_dir_writes_empty_file(tmp_path):
    """A zero-page crawl must write a 0-byte file, not a hollow shell — that's
    what trips orchestrate's EmptyOutputError before staging clobbers a prior
    good deliverable."""
    raw = tmp_path / "raw"
    raw.mkdir()
    acq = AcquireResult(raw_dir=raw, kind="html", slug="x", pages=0, title="X Manual")
    out = DocsProbePattern().normalize(acq, tmp_path)
    assert out.stat().st_size == 0


def test_normalize_html_escapes_title(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "0000-a.html").write_text("<section><h1>A</h1></section>", encoding="utf-8")
    acq = AcquireResult(raw_dir=raw, kind="html", slug="x", pages=1, title="Tips & <Tricks>")
    out = DocsProbePattern().normalize(acq, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "<title>Tips &amp; &lt;Tricks&gt;</title>" in text


def test_a_url_that_serves_a_pdf_is_handed_to_pdf_url(tmp_path, monkeypatch):
    """Celemony serves the Melodyne manual as application/pdf from an
    extensionless path (/M5/pdf/melodyneStudio5/en), so pdf_url.match declines
    it and this catch-all receives it. docs_probe is the *content* prober — a
    PDF body must route, not raise 'unrecognized docs site'."""
    monkeypatch.setattr(http, "fetch_text", lambda u, **k: (u, "%PDF-1.7 body"))
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda u, **k: (u, b"%PDF-1.7 body", {"etag": None, "last_modified": None}),
    )

    probe = DocsProbePattern()
    acq = probe.acquire("https://helpcenter.celemony.com/M5/pdf/melodyneStudio5/en", tmp_path)

    assert acq.kind == "pdf"
    assert next(acq.raw_dir.glob("*.pdf")).exists()

    # normalize must pass the PDF through. Globbing *.html here finds nothing and
    # writes a 0-byte file, which orchestrate rejects as EmptyOutputError.
    out = probe.normalize(acq, tmp_path)
    assert out.suffix == ".pdf"
    assert out.read_bytes().startswith(b"%PDF-")


def test_soft_404_search_index_is_not_mistaken_for_mkdocs(tmp_path, monkeypatch):
    """The MkDocs probe must validate the index, not just that the URL fetched.

    synchroarts.com returns 200 with HTML for arbitrary paths, so
    `search/search_index.json` "existed" and docs_probe reported
    `generator=mkdocs via=search_index` — then _mkdocs.acquire rejected it. A
    site with a catch-all 200 gets misrouted and the real error is masked.
    """
    home = "<html><head><title>Manual</title></head><body><article>x</article></body></html>"

    def fetch(url, **kwargs):
        if url.endswith("search/search_index.json"):
            return url, home  # soft 404: HTML, not an index
        if url.endswith("/llms.txt"):
            raise OSError("404")
        return url, home

    monkeypatch.setattr(http, "fetch_text", fetch)

    with pytest.raises(InvalidInputError, match="unrecognized docs site"):
        DocsProbePattern().acquire("https://www.synchroarts.com/manuals/x/welcome.html", tmp_path)


def test_clickhelp_is_detected_without_a_generator_meta(tmp_path, monkeypatch):
    """ClickHelp ships no <meta name="generator">, so the meta sniff can never
    claim it — detection has to key on its own asset tells."""
    from pagespring.patterns import _clickhelp

    home = (
        '<html><head><link href="../_webHelpStyles/CHWebHelp.css"></head>'
        '<body class="WebHelp_body"><div id="pnlTopicContentContainer">x</div></body></html>'
    )
    monkeypatch.setattr(http, "fetch_text", lambda u, **k: (u, home))
    called = {}

    def spy(url, workdir, *, slug, title):
        called["url"] = url
        raw = workdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        return AcquireResult(raw_dir=raw, kind="html", slug=slug, pages=1, title=title)

    monkeypatch.setattr(_clickhelp, "acquire", spy)

    acq = DocsProbePattern().acquire(
        "https://vendor.example/manuals/Widget/Manual/HTML/welcome.html", tmp_path
    )

    assert called["url"].endswith("/HTML/welcome.html")
    assert acq.slug == "widget", "slug must come from the manual path, not the host"


def test_llms_txt_delegation_keeps_the_probed_slug_and_title(tmp_path, monkeypatch):
    """docs_probe derives the slug from the host and the title from the page. Delegating
    to GitBook without them folds every custom domain onto its generic host label
    ('help', 'docs'), where they collide."""
    home = (
        "<html><head><title>Widget Pro Manual</title>"
        '<meta name="generator" content="nothing-known"></head><body>x</body></html>'
    )
    llms = "- [Intro](https://help.widgetpro.com/intro.md)\n"

    def fake_fetch(url, **kw):
        if url.endswith("/llms.txt"):
            return url, llms
        if url.endswith(".md"):
            return url, "# Intro\n\nbody\n"
        return url, home

    monkeypatch.setattr(docs_probe.http, "fetch_text", fake_fetch)
    monkeypatch.setattr(
        docs_probe, "_fetch_or_none", lambda url: llms if url.endswith("llms.txt") else None
    )
    monkeypatch.setattr(gitbook.http, "fetch_text", fake_fetch)
    monkeypatch.setattr(gitbook.http, "polite_sleep", lambda *a, **k: None)

    acq = docs_probe.DocsProbePattern().acquire("https://help.widgetpro.com/", tmp_path)

    assert acq.slug == "widgetpro", f"probed slug discarded, got {acq.slug!r}"
    assert acq.title == "Widget Pro Manual", "probed title discarded"


def _spy_gitbook_acquire(called):
    def spy(self, url, workdir, *, slug=None, title=None, rendered=True):
        called.update(url=url, slug=slug, title=title, rendered=rendered)
        raw = workdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        return AcquireResult(raw_dir=raw, kind="markdown", slug=slug, pages=2, title=title)

    return spy


def test_llms_txt_rung_uses_the_index_nearest_the_seed(tmp_path, monkeypatch):
    """A docs subpath often publishes its own llms.txt; the site root's lists other pages."""
    home = "<html><head><title>Acme Docs</title></head><body>x</body></html>"
    indexes = {
        "https://acme.example/llms.txt": "- [Pricing](https://acme.example/pricing.md)\n",
        "https://acme.example/docs/llms.txt": (
            "- [Intro](https://acme.example/docs/intro.md)\n"
            "- [Setup](https://acme.example/docs/setup.md)\n"
        ),
    }
    monkeypatch.setattr(http, "fetch_text", lambda url, **k: (url, home))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    monkeypatch.setattr(docs_probe, "_fetch_or_none", indexes.get)
    called = {}
    monkeypatch.setattr(gitbook.GitBookPattern, "acquire", _spy_gitbook_acquire(called))

    DocsProbePattern().acquire("https://acme.example/docs/intro", tmp_path)

    assert called == {
        "url": "https://acme.example/docs",
        "slug": "acme",
        "title": "Acme Docs",
        "rendered": True,
    }


@pytest.mark.parametrize(
    ("generator", "rendered"),
    [("", True), ("GitBook (931cbe7)", True), ("Mintlify", False)],
    ids=["no-generator", "gitbook", "other-platform"],
)
def test_llms_txt_rung_fetches_rendered_pages_only_for_gitbook(
    tmp_path, monkeypatch, generator, rendered
):
    """Only a GitBook page needs its rendered twin, to resolve /files/<id> images."""
    meta = f'<meta name="generator" content="{generator}">' if generator else ""
    home = f"<html><head>{meta}<title>R</title></head><body>x</body></html>"
    index = "- [Intro](https://docs.example.com/intro.md)\n"
    monkeypatch.setattr(http, "fetch_text", lambda url, **k: (url, home))
    monkeypatch.setattr(
        docs_probe, "_fetch_or_none", lambda url: index if url.endswith("/llms.txt") else None
    )
    called = {}
    monkeypatch.setattr(gitbook.GitBookPattern, "acquire", _spy_gitbook_acquire(called))

    DocsProbePattern().acquire("https://docs.example.com/", tmp_path)

    assert called["rendered"] is rendered


def test_an_extensionless_url_serving_openapi_is_ingested_as_the_spec(tmp_path, monkeypatch):
    """springdoc serves specs from /v3/api-docs/<group>, so api_spec.match declines
    them — and a UI page's several-specs refusal lists exactly those URLs to ingest."""
    url = "https://demo.example/v3/api-docs/users"
    spec_text = (
        '{"openapi": "3.0.1", "info": {"title": "Users API", "version": "2.9.1"}, '
        '"paths": {"/users": {"get": {"summary": "List users"}}}}'
    )
    fetched: list[str] = []

    def fetch(u, **k):
        fetched.append(u)
        return u, spec_text

    monkeypatch.setattr(http, "fetch_text", fetch)
    probe = DocsProbePattern()

    acq = probe.acquire(url, tmp_path)
    out = probe.normalize(acq, tmp_path)

    assert (acq.kind, acq.slug, acq.single_document) == ("markdown", "users-api-2-9-1", True)
    assert "/users" in out.read_text(encoding="utf-8")
    assert fetched == [url]


@pytest.mark.parametrize(
    "body",
    [
        '{"name": "Acme API", "swagger": "https://api.acme.example/swagger/v1/swagger.json"}',
        '{"docs": "/docs", "openapi": "/openapi.json"}',
    ],
    ids=["swagger-link", "openapi-link"],
)
def test_an_api_root_that_only_links_its_spec_is_not_taken_for_the_spec(
    tmp_path, monkeypatch, body
):
    monkeypatch.setattr(http, "fetch_text", lambda u, **k: (u, body))

    with pytest.raises(InvalidInputError, match="unrecognized docs site"):
        DocsProbePattern().acquire("https://api.acme.example/", tmp_path)


def test_a_pdf_too_large_for_the_text_budget_is_handed_to_pdf_url(tmp_path, monkeypatch):
    """The home page is read under the text budget; a vendor PDF can exceed it."""
    url = "https://vendor.example/manual/pdf"

    def too_big(u, **k):
        raise ClientError("response exceeded max_bytes", context={"url": u, "max_bytes": 25})

    monkeypatch.setattr(http, "fetch_text", too_big)
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda u, **k: (u, b"%PDF-1.7 body", {"etag": None, "last_modified": None}),
    )

    acq = DocsProbePattern().acquire(url, tmp_path)

    assert acq.kind == "pdf"


def test_a_spec_ui_page_ingests_the_spec_it_names(tmp_path, monkeypatch):
    spec = "https://docs.vendor.example/api/openapi.yaml"
    home = f'<html><head><title>API</title></head><body><redoc spec-url="{spec}"></redoc></body></html>'
    spec_text = (
        "openapi: 3.0.0\ninfo:\n  title: Vendor API\n  version: '2'\n"
        "paths:\n  /ping:\n    get:\n      summary: Ping\n"
    )
    monkeypatch.setattr(
        http, "fetch_text", lambda url, **k: (url, spec_text if url == spec else home)
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    probe = DocsProbePattern()

    acq = probe.acquire("https://docs.vendor.example/api/", tmp_path)
    out = probe.normalize(acq, tmp_path)

    assert (acq.kind, acq.slug, acq.single_document) == ("markdown", "vendor-api-2", True)
    assert "/ping" in out.read_text(encoding="utf-8")


def test_a_spec_ui_page_naming_no_spec_is_refused_by_name(tmp_path, monkeypatch):
    home = '<html><body><div id="swagger-ui"></div><script src="./swagger-ui-bundle.js"></script></body></html>'
    monkeypatch.setattr(http, "fetch_text", lambda url, **k: (url, home))

    with pytest.raises(InvalidInputError, match="swagger-ui page"):
        DocsProbePattern().acquire("https://api.vendor.example/docs", tmp_path)


_OPENAPI_BODY = '{"openapi": "3.0.0", "info": {"title": "Vendor API"}, "paths": {}}'


@pytest.mark.parametrize(
    ("home", "files", "dispatch"),
    [
        (
            '<meta name="generator" content="Mintlify"><main id="content-container"></main>',
            {"https://docs.vendor.example/docs/llms.txt": "https://docs.vendor.example/docs/a.md"},
            ("gitbook", "https://docs.vendor.example/docs", False),
        ),
        (
            '<meta name="generator" content="https://buildwithfern.com"><main class="fern-main">',
            {"https://docs.vendor.example/docs/llms.txt": "https://docs.vendor.example/docs/a.md"},
            ("gitbook", "https://docs.vendor.example/docs", False),
        ),
        (
            '<main id="content" class="rm-Guides"><article class="rm-Article"></article></main>',
            {"https://docs.vendor.example/docs/llms.txt": "https://docs.vendor.example/docs/a.md"},
            ("gitbook", "https://docs.vendor.example/docs", True),
        ),
        (
            '<article id="nd-page" class="flex flex-col"></article>',
            {"https://docs.vendor.example/llms.txt": "https://docs.vendor.example/docs/a.md"},
            ("gitbook", "https://docs.vendor.example", True),
        ),
        (
            '<meta name="generator" content="Hugo 0.99.1"><body class="td-section"><main></main>',
            {},
            ("hugo", "https://docs.vendor.example/docs", None),
        ),
        (
            '<meta name="generator" content="Hugo 0.165.0">'
            '<article class="gdoc-markdown" id="main-content"></article>',
            {},
            ("hugo", "https://docs.vendor.example/docs", None),
        ),
        (
            '<redoc spec-url="/openapi.json"></redoc>',
            {"https://docs.vendor.example/openapi.json": _OPENAPI_BODY},
            ("api_spec", "https://docs.vendor.example/openapi.json", None),
        ),
        (
            '<div id="swagger-ui"></div><script src="./swagger-ui-bundle.js"></script>'
            "<script>SwaggerUIBundle({ url: '/openapi.json', dom_id: '#swagger-ui' })</script>",
            {"https://docs.vendor.example/openapi.json": _OPENAPI_BODY},
            ("api_spec", "https://docs.vendor.example/openapi.json", None),
        ),
        (
            '<script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>'
            "<script>Scalar.createApiReference('#app', { url: '/openapi.json' })</script>",
            {"https://docs.vendor.example/openapi.json": _OPENAPI_BODY},
            ("api_spec", "https://docs.vendor.example/openapi.json", None),
        ),
    ],
    ids=[
        "mintlify",
        "fern",
        "readme",
        "fumadocs",
        "docsy-hugo",
        "geekdoc",
        "redoc",
        "swagger-ui",
        "scalar",
    ],
)
def test_platforms_without_a_pattern_of_their_own_route_through_existing_rungs(
    tmp_path, monkeypatch, home, files, dispatch
):
    """These platforms need no pattern named for them: a generator tag, an llms.txt index,
    or an API reference UI already routes them. Measured on live sites; before proposing a
    pattern for a platform, check routing like this rather than searching src/ for its name."""
    page = f"<html><head><title>Vendor Docs</title></head><body>{home}</body></html>"

    def fetch(url, **kwargs):
        if url in files:
            return url, files[url]
        if url.endswith(("llms.txt", "search_index.json", ".json")):
            raise OSError("404")
        return url, page

    seen = []

    def record(name):
        def spy(*args, **kwargs):
            url = next(a for a in args if isinstance(a, str))
            seen.append((name, url.rstrip("/"), kwargs.get("rendered")))
            raise _Dispatched

        return spy

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    monkeypatch.setattr(_hugo, "acquire", record("hugo"))
    monkeypatch.setattr(gitbook.GitBookPattern, "acquire", record("gitbook"))
    monkeypatch.setattr(docs_probe.ApiSpecPattern, "acquire", record("api_spec"))

    with pytest.raises(_Dispatched):
        DocsProbePattern().acquire("https://docs.vendor.example/docs", tmp_path)

    assert seen == [dispatch]


class _Dispatched(Exception):
    pass


def test_a_trailing_slash_seed_resolves_relative_spec_urls_below_it(tmp_path, monkeypatch):
    """The ladder fetches the seed without its trailing slash; with no redirect to put it
    back, a relative spec URL would resolve one directory too high."""
    spec = "https://api.vendor.example/docs/openapi.json"
    home = '<html><body><redoc spec-url="openapi.json"></redoc></body></html>'
    spec_text = '{"openapi": "3.0.0", "info": {"title": "Vendor API"}, "paths": {}}'
    events = []

    def fetch(url, **kwargs):
        events.append(url)
        return url, spec_text if url == spec else home

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: events.append("sleep"))

    DocsProbePattern().acquire("https://api.vendor.example/docs/", tmp_path)

    assert events == ["https://api.vendor.example/docs", "sleep", spec]


def test_a_swagger_ui_page_ingests_the_spec_its_url_query_names(tmp_path, monkeypatch):
    """A stock Swagger UI dist is pointed at a spec by its page query; the initializer
    it ships still names Swagger's demo spec."""
    page = "https://api.vendor.example/swagger/index.html?url=/api/openapi.json"
    home = (
        '<html><body><div id="swagger-ui"></div><script src="./swagger-ui-bundle.js"></script>'
        '<script src="./swagger-initializer.js"></script></body></html>'
    )
    bodies = {
        page: home,
        "https://api.vendor.example/swagger/swagger-initializer.js": (
            'window.ui = SwaggerUIBundle({ url: "https://petstore.swagger.io/v2/swagger.json" });'
        ),
        "https://petstore.swagger.io/v2/swagger.json": (
            '{"swagger": "2.0", "info": {"title": "Swagger Petstore", "version": "1.0.7"}}'
        ),
        "https://api.vendor.example/api/openapi.json": (
            '{"openapi": "3.0.0", "info": {"title": "Vendor API", "version": "2"}, "paths": {}}'
        ),
    }
    monkeypatch.setattr(http, "fetch_text", lambda url, **k: (url, bodies[url]))

    acq = DocsProbePattern().acquire(page, tmp_path)

    assert (acq.slug, acq.title) == ("vendor-api-2", "Vendor API 2")


def test_the_llms_txt_route_paces_every_request(tmp_path, monkeypatch):
    home = "<html><head><title>Acme Docs</title></head><body>x</body></html>"
    index = "- [Intro](https://acme.example/docs/intro.md)\n"
    events = []

    def fetch(url, **kwargs):
        events.append(url)
        if url == "https://acme.example/llms.txt":
            return url, index
        if url.endswith(("llms.txt", "search_index.json")):
            raise OSError("404")
        return url, home

    def gitbook_acquire(self, url, workdir, **kwargs):
        events.append("gitbook")
        raw = workdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        return AcquireResult(raw_dir=raw, kind="markdown", slug="acme", pages=1, title="Acme")

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: events.append("sleep"))
    monkeypatch.setattr(gitbook.GitBookPattern, "acquire", gitbook_acquire)

    DocsProbePattern().acquire("https://acme.example/docs/intro", tmp_path)

    assert events[-1] == "gitbook"
    unpaced = [e for i, e in enumerate(events) if i and e != "sleep" and events[i - 1] != "sleep"]
    assert unpaced == [], events


def test_llms_txt_rung_skips_an_html_page_served_at_a_candidate_path(tmp_path, monkeypatch):
    home = "<html><head><title>Acme Docs</title></head><body>x</body></html>"
    indexes = {
        "https://acme.example/docs/llms.txt": (
            '<!DOCTYPE html><html><body><a href="https://acme.example/docs/x.md">x</a>'
            "</body></html>"
        ),
        "https://acme.example/llms.txt": "- [Intro](https://acme.example/docs/intro.md)\n",
    }
    monkeypatch.setattr(http, "fetch_text", lambda url, **k: (url, home))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    monkeypatch.setattr(docs_probe, "_fetch_or_none", indexes.get)
    called = {}
    monkeypatch.setattr(gitbook.GitBookPattern, "acquire", _spy_gitbook_acquire(called))

    DocsProbePattern().acquire("https://acme.example/docs/intro", tmp_path)

    assert called["url"] == "https://acme.example"


def test_an_oversize_body_that_is_not_a_pdf_reports_the_size_limit(tmp_path, monkeypatch):
    """A single-page HTML manual can outgrow the text budget too; the useful error is
    the budget, not "not a PDF"."""
    oversize = ClientError("response exceeded max_bytes", context={"url": "u", "max_bytes": 25})

    def too_big(u, **k):
        raise oversize

    monkeypatch.setattr(http, "fetch_text", too_big)
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda u, **k: (
            u,
            b"<!DOCTYPE html><html>" + b"x" * 64,
            {"etag": None, "last_modified": None},
        ),
    )

    with pytest.raises(ClientError, match="max_bytes"):
        DocsProbePattern().acquire("https://vendor.example/manual/all-in-one", tmp_path)
