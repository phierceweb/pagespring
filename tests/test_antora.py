"""_antora — the entry-page tell, and a nav-ordered crawl of one component version
topped up from the component's own sitemap (mocked fetch)."""

from urllib.error import HTTPError

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.patterns import _antora
from pagespring.patterns.docs_probe import DocsProbePattern

_O = "https://docs.example.com"
_V = f"{_O}/widget/latest/"

# The entry nav as Antora renders it: relative hrefs, a page-less heading, a repeat, a fragment, an
# offsite link, another component and an attachment.
_ENTRY_NAV = """
<ul class="nav-list"><li class="nav-item" data-depth="0"><ul class="nav-list">
<li class="nav-item"><a class="nav-link" href="config/">Configure</a>
  <ul class="nav-list">
  <li class="nav-item"><a class="nav-link" href="config/advanced/">Advanced</a></li>
  </ul></li>
<li class="nav-item"><span class="nav-text">Reference</span>
  <ul class="nav-list">
  <li class="nav-item"><a class="nav-link" href="install/">Install</a></li>
  <li class="nav-item"><a class="nav-link" href="install/#linux">Install on Linux</a></li>
  <li class="nav-item"><a class="nav-link" href="config/">Configure again</a></li>
  <li class="nav-item"><a class="nav-link" href="https://github.com/ex/widget">Source</a></li>
  <li class="nav-item"><a class="nav-link" href="../../gadget/latest/">Gadget</a></li>
  <li class="nav-item"><a class="nav-link" href="_attachments/cheatsheet.pdf">PDF</a></li>
  </ul></li>
</ul></li></ul>"""

_DEEP_NAV = """
<ul class="nav-list">
<li class="nav-item"><a class="nav-link" href="/widget/latest/config/">Configure</a></li>
<li class="nav-item"><a class="nav-link" href="/widget/latest/config/advanced/">Advanced</a></li>
<li class="nav-item"><a class="nav-link" href="/widget/latest/install/">Install</a></li>
</ul>"""


def _page(
    heading: str | None,
    body: str,
    *,
    nav: str = _DEEP_NAV,
    start: str = "/widget/latest/",
    generator: bool = True,
) -> str:
    h1 = f'<h1 class="page">{heading}</h1>' if heading else ""
    meta = '<meta name="generator" content="Antora 3.2.0">' if generator else ""
    return f"""<!DOCTYPE html><html><head><title>{heading} :: Example Docs</title>{meta}
</head><body class="article"><header class="header"><nav class="navbar">TOPBAR</nav></header>
<div class="body"><div class="nav-container" data-component="widget" data-version="2.0">
<aside class="nav"><div class="panels"><div class="nav-panel-menu is-active" data-panel="menu">
<nav class="nav-menu"><h3 class="title"><a href="{start}">Widget</a></h3>{nav}</nav></div>
<div class="nav-panel-explore" data-panel="explore"><div class="context">
<span class="title">Widget</span><span class="version">2.0</span></div>
<ul class="components"><li class="component is-current">EXPLORE</li></ul></div></div></aside></div>
<main class="article"><div class="toolbar" role="navigation">
<nav class="breadcrumbs"><ul><li><a href="{start}">CRUMB</a></li></ul></nav>
<div class="edit-this-page"><a href="https://github.com/ex/edit">Edit this Page</a></div></div>
<div class="content"><aside class="toc sidebar"><div class="toc-menu">TOCMENU</div></aside>
<article class="doc">{h1}{body}
<nav class="pagination"><span class="next"><a href="../">PAGINATION</a></span></nav></article>
</div></main></div><footer class="footer">FOOTER</footer>
<script src="../../_/js/site.js"></script></body></html>"""


_INDEX = f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>{_O}/sitemap-ROOT.xml</loc></sitemap>
<sitemap><loc>{_O}/sitemap-gadget.xml</loc></sitemap>
<sitemap><loc>{_O}/sitemap-widget.xml</loc></sitemap>
</sitemapindex>"""


def _urlset(*locs: str) -> str:
    urls = "".join(f"<url><loc>{u}</loc></url>" for u in locs)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    )


_WIDGET_SITEMAP = _urlset(
    _V,
    f"{_V}config/",
    f"{_V}config/advanced/",
    f"{_V}install/",
    f"{_V}orphan/",
    f"{_V}tags/foo/",
    f"{_O}/widget/1.0/",
    f"{_O}/widget/1.0/install/",
)


def _site() -> dict[str, str]:
    return {
        f"{_O}/sitemap.xml": _INDEX,
        f"{_O}/sitemap-widget.xml": _WIDGET_SITEMAP,
        _V: _page(
            "Widget Documentation",
            '<p>Welcome.</p><img src="_images/logo.png" srcset="_images/logo@2x.png 2x">'
            "<script>track()</script>",
            nav=_ENTRY_NAV,
            start="./",
        ),
        f"{_V}config/": _page("Configure", "<p>Set the options.</p>"),
        f"{_V}config/advanced/": _page("Advanced", "<p>Tune it.</p>"),
        f"{_V}install/": _page("Install", '<p>Run it.</p><a href="../config/">config</a>'),
        f"{_V}orphan/": _page("CLI Options", "<p>Every flag.</p>"),
        f"{_V}tags/foo/": _page(None, "<h2>Pages with tag foo</h2>"),
        f"{_O}/gadget/latest/": _page("Gadget", "<p>Other component.</p>"),
        f"{_O}/widget/1.0/": _page("Old", "<p>Old version.</p>"),
    }


@pytest.fixture
def site(monkeypatch):
    pages = _site()
    seen: list[str] = []

    def fake(url, **kwargs):
        seen.append(url)
        if url not in pages:
            raise HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
        return url, pages[url]

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    return pages, seen


def _staged(acq) -> list[str]:
    return [p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html"))]


def _sources(acq) -> list[str]:
    return [t.split(" -->", 1)[0].removeprefix("<!-- source: ") for t in _staged(acq)]


# --- the tell ---------------------------------------------------------------


def test_the_generator_tag_names_antora():
    assert _antora.is_antora(_page("X", "<p>x</p>"))


def test_the_layout_names_antora_when_the_ui_drops_the_generator_tag():
    assert _antora.is_antora(_page("X", "<p>x</p>", generator=False))


@pytest.mark.parametrize(
    "html",
    [
        # rustdoc also has a .nav-container, but no article.doc
        '<html><body><div class="nav-container">n</div><main>m</main></body></html>',
        # Docusaurus / Hugo themes use a bare <article>
        '<html><head><meta name="generator" content="Docusaurus v3"></head>'
        "<body><nav>n</nav><article><h1>T</h1></article></body></html>",
        # an article.doc on its own is not the Antora layout
        '<html><body><article class="doc"><p>x</p></article></body></html>',
        "<html><body><p>plain</p></body></html>",
    ],
)
def test_other_generators_are_not_antora(html):
    assert not _antora.is_antora(html)


# --- discovery and order ----------------------------------------------------


def test_pages_follow_nav_order_with_the_start_page_first(tmp_path, site):
    acq = _antora.acquire(_V.rstrip("/"), tmp_path, slug="example", title=None)

    assert _sources(acq)[:4] == [_V, f"{_V}config/", f"{_V}config/advanced/", f"{_V}install/"]


def test_a_page_the_nav_leaves_out_comes_from_the_sitemap_after_the_nav(tmp_path, site):
    acq = _antora.acquire(_V, tmp_path, slug="example", title=None)

    assert _sources(acq)[4:] == [f"{_V}orphan/"]
    assert acq.pages == 5


def test_a_page_at_a_long_path_stages_under_a_short_file_name(tmp_path, site):
    pages, _seen = site
    long_page = f"{_V}{'a' * 300}/"  # past a file name's 255-byte limit
    pages[f"{_O}/sitemap-widget.xml"] = _urlset(_V, long_page)
    pages[long_page] = _page("Long", "<p>Long body.</p>")
    acq = _antora.acquire(_V, tmp_path, slug="example", title=None)

    assert _sources(acq)[-1] == long_page
    assert max(len(p.name) for p in acq.raw_dir.iterdir()) <= 100


def test_other_versions_and_components_stay_out(tmp_path, site):
    _pages, seen = site
    _antora.acquire(_V, tmp_path, slug="example", title=None)

    assert not any("/widget/1.0/" in u for u in seen)
    assert not any("/gadget/" in u for u in seen)
    assert not any("github.com" in u or u.endswith(".pdf") for u in seen)


def test_only_the_components_own_sitemap_is_read(tmp_path, site):
    _pages, seen = site
    _antora.acquire(_V, tmp_path, slug="example", title=None)

    assert f"{_O}/sitemap-widget.xml" in seen
    assert f"{_O}/sitemap-gadget.xml" not in seen
    assert f"{_O}/sitemap-ROOT.xml" not in seen


def test_without_a_component_name_the_version_path_names_its_sitemap(tmp_path, site):
    pages, seen = site
    for url in list(pages):
        pages[url] = pages[url].replace(' data-component="widget"', "")

    acq = _antora.acquire(_V, tmp_path, slug="example", title=None)

    assert f"{_O}/sitemap-widget.xml" in seen
    assert f"{_V}orphan/" in _sources(acq)


def test_the_sitemap_is_found_by_walking_up_from_the_version(tmp_path, site):
    _pages, seen = site
    _antora.acquire(_V, tmp_path, slug="example", title=None)

    sitemaps = [u for u in seen if u.endswith("sitemap.xml")]
    assert sitemaps == [f"{_V}sitemap.xml", f"{_O}/widget/sitemap.xml", f"{_O}/sitemap.xml"]


def test_each_page_is_fetched_once(tmp_path, site):
    _pages, seen = site
    _antora.acquire(_V, tmp_path, slug="example", title=None)

    pages = [u for u in seen if not u.endswith(".xml")]
    assert len(pages) == len(set(pages))


def test_a_generated_listing_the_nav_leaves_out_is_skipped_not_lost(tmp_path, site):
    acq = _antora.acquire(_V, tmp_path, slug="example", title=None)

    assert f"{_V}tags/foo/" not in _sources(acq)
    assert acq.lost == 0


def test_the_entry_page_is_kept_without_a_page_title(tmp_path, site):
    """A component's home can use a landing layout with no h1.page."""
    pages, _seen = site
    pages[_V] = pages[_V].replace('<h1 class="page">Widget Documentation</h1>', "")

    acq = _antora.acquire(_V, tmp_path, slug="example", title=None)

    assert _sources(acq)[0] == _V


def test_a_deep_seed_crawls_its_whole_component_version(tmp_path, site):
    acq = _antora.acquire(f"{_V}config/advanced/", tmp_path, slug="example", title=None)

    assert _sources(acq)[:4] == [_V, f"{_V}config/", f"{_V}config/advanced/", f"{_V}install/"]


def test_a_non_index_start_page_still_scopes_to_the_version(tmp_path, site):
    """The version root can be a redirect to a start page further down."""
    pages, _seen = site
    nav = _DEEP_NAV.replace("/widget/latest/config/advanced/", "/widget/latest/intro/more/")
    pages[f"{_V}intro/"] = _page(
        "Intro", "<p>Start here.</p>", nav=nav, start="/widget/latest/intro/"
    )
    pages[f"{_V}intro/more/"] = _page("More", "<p>More.</p>")

    acq = _antora.acquire(f"{_V}intro/", tmp_path, slug="example", title=None)

    assert f"{_V}install/" in _sources(acq)
    assert f"{_V}config/" in _sources(acq)


def test_without_a_sitemap_the_nav_alone_is_the_page_set(tmp_path, site):
    pages, _seen = site
    del pages[f"{_O}/sitemap.xml"]

    acq = _antora.acquire(_V, tmp_path, slug="example", title=None)

    assert _sources(acq) == [_V, f"{_V}config/", f"{_V}config/advanced/", f"{_V}install/"]


def test_a_single_urlset_sitemap_is_read_directly(tmp_path, site):
    pages, _seen = site
    pages[f"{_O}/sitemap.xml"] = _WIDGET_SITEMAP

    acq = _antora.acquire(_V, tmp_path, slug="example", title=None)

    assert _sources(acq)[4:] == [f"{_V}orphan/"]


def test_a_directory_seed_gets_its_slash_back(tmp_path, site):
    _pages, seen = site
    _antora.acquire(_V.rstrip("/"), tmp_path, slug="example", title=None)

    assert seen[0] == _V


def test_an_extensionless_page_seed_is_fetched_as_given(tmp_path, site):
    """With extensions dropped, a page URL looks like a directory until the slash 404s."""
    pages, seen = site
    solo = f"{_V}solo"
    pages[solo] = _page("Solo", "<p>Alone.</p>").replace(
        '<nav class="nav-menu">', '<nav class="x">'
    )
    del pages[f"{_O}/sitemap.xml"]

    acq = _antora.acquire(solo, tmp_path, slug="example", title=None)

    assert seen[:2] == [f"{solo}/", solo]
    assert _sources(acq) == [solo]


# --- extraction -------------------------------------------------------------


def test_article_is_extracted_without_chrome(tmp_path, site):
    acq = _antora.acquire(_V, tmp_path, slug="example", title=None)
    joined = "\n".join(_staged(acq))

    assert "Set the options." in joined
    assert '<h1 class="page">Configure</h1>' in joined
    for chrome in ("TOPBAR", "EXPLORE", "TOCMENU", "CRUMB", "Edit this Page", "PAGINATION"):
        assert chrome not in joined
    assert "FOOTER" not in joined
    assert "track()" not in joined


def test_a_landing_layouts_search_box_and_comments_are_dropped(tmp_path, site):
    pages, _seen = site
    pages[f"{_V}install/"] = pages[f"{_V}install/"].replace(
        "<p>Run it.</p>",
        '<div id="search-field"><input id="search-input" placeholder="SEARCHBOX"></div>'
        "<!-- <a href='/retired/'>RETIRED</a> --><p>Run it.</p>",
    )

    acq = _antora.acquire(_V, tmp_path, slug="example", title=None)
    joined = "\n".join(_staged(acq))

    assert "Run it." in joined
    assert "SEARCHBOX" not in joined
    assert "RETIRED" not in joined


def test_refs_are_absolute_and_responsive_images_flattened(tmp_path, site):
    acq = _antora.acquire(_V, tmp_path, slug="example", title=None)
    joined = "\n".join(_staged(acq))

    assert f'src="{_V}_images/logo@2x.png"' in joined
    assert "srcset" not in joined
    assert f'href="{_V}config/"' in joined


def test_a_page_without_article_doc_counts_as_lost(tmp_path, site):
    pages, _seen = site
    pages[f"{_V}install/"] = "<html><body><div id='content'>renamed</div></body></html>"

    acq = _antora.acquire(_V, tmp_path, slug="example", title=None)

    assert acq.lost == 1
    assert f"{_V}install/" not in _sources(acq)


def test_a_failed_fetch_counts_as_lost(tmp_path, site):
    pages, _seen = site
    del pages[f"{_V}config/advanced/"]

    acq = _antora.acquire(_V, tmp_path, slug="example", title=None)

    assert acq.lost == 1
    assert acq.pages == 4


def test_identical_content_under_two_urls_is_staged_once(tmp_path, site):
    pages, _seen = site
    pages[f"{_V}orphan/"] = pages[f"{_V}install/"]

    acq = _antora.acquire(_V, tmp_path, slug="example", title=None)

    assert acq.pages == 4
    assert acq.lost == 0


def test_a_redirect_out_of_the_version_is_not_staged(tmp_path, monkeypatch, site):
    pages, _seen = site
    inner = http.fetch_text

    def fake(url, **kwargs):
        if url == f"{_V}install/":
            return f"{_O}/widget/1.0/", pages[f"{_O}/widget/1.0/"]
        return inner(url, **kwargs)

    monkeypatch.setattr(http, "fetch_text", fake)

    acq = _antora.acquire(_V, tmp_path, slug="example", title=None)

    assert "Old version." not in "\n".join(_staged(acq))


def test_no_article_anywhere_is_an_input_error(tmp_path, site):
    pages, _seen = site
    for url in list(pages):
        if not url.endswith(".xml"):
            pages[url] = pages[url].replace('class="doc"', 'class="renamed"')

    with pytest.raises(InvalidInputError):
        _antora.acquire(_V, tmp_path, slug="example", title=None)


# --- result shape -----------------------------------------------------------


def test_the_page_cap_truncates(tmp_path, monkeypatch, site):
    monkeypatch.setattr(_antora, "_MAX_PAGES", 2)

    acq = _antora.acquire(_V, tmp_path, slug="example", title=None)

    assert acq.pages == 2
    assert acq.truncated


def test_slug_names_the_component_version_and_title_its_explore_context(tmp_path, site):
    acq = _antora.acquire(_V, tmp_path, slug="example", title="Widget Documentation :: Ex")

    assert acq.slug == "example-widget-latest"
    assert acq.title == "Widget 2.0"
    assert acq.kind == "html"
    assert not acq.truncated
    assert not acq.single_document


def test_a_repeated_segment_is_not_repeated_in_the_slug(tmp_path, site):
    acq = _antora.acquire(_V, tmp_path, slug="widget", title=None)

    assert acq.slug == "widget-latest"


def test_a_one_page_component_confirmed_by_its_sitemap_is_a_single_document(tmp_path, monkeypatch):
    """A landing-layout home: no nav, no h1.page, and a sitemap below the host root."""
    home = f"{_O}/en-US/docs/"
    landing = (
        '<html><head><meta name="generator" content="Antora 3.1.15"><title>Docs Home</title>'
        '</head><body class="article"><main class="main"><article class="doc">'
        '<div class="homepage-page"><h2>User Documentation</h2>'
        '<a href="../fedora/latest/">Fedora Linux</a></div></article></main></body></html>'
    )
    index = _INDEX.replace(f"{_O}/sitemap-", f"{_O}/en-US/sitemap-").replace("widget", "docs")
    pages = {
        f"{_O}/en-US/sitemap.xml": index,
        f"{_O}/en-US/sitemap-docs.xml": _urlset(home),
        home: landing,
    }
    seen: list[str] = []

    def fake(url, **kwargs):
        seen.append(url)
        if url not in pages:
            raise HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
        return url, pages[url]

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _antora.acquire(home.rstrip("/"), tmp_path, slug="fedoraproject", title="Docs Home")

    assert acq.pages == 1
    assert acq.single_document
    assert acq.slug == "fedoraproject-docs"
    assert acq.title == "Docs Home"
    assert f"{_O}/fedora/latest/" not in seen


def test_docs_probe_normalize_merges_pages_in_nav_order(tmp_path, site):
    acq = _antora.acquire(_V, tmp_path, slug="example", title=None)

    out = DocsProbePattern().normalize(acq, tmp_path).read_text(encoding="utf-8")

    order = [
        out.index(t) for t in ("Welcome.", "Set the options.", "Tune it.", "Run it.", "Every flag.")
    ]
    assert order == sorted(order)
    assert "<title>Widget 2.0</title>" in out
