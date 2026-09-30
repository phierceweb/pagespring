"""_vitepress — sidebar-driven crawl of VitePress default-theme sites (mocked fetch)."""

from urllib.error import HTTPError

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.patterns import _vitepress
from pagespring.patterns.docs_probe import DocsProbePattern

_HOST = "https://ex.dev"
_GUIDE = [
    ("/guide/why", "Why"),
    ("/guide/", "Getting Started"),
    ("/guide/features", "Features"),
    ("/reference/config", "Config Reference"),
]
_REFERENCE = [("/reference/config", "Config Reference"), ("/reference/cli", "CLI")]


def _page(
    title: str,
    body: str,
    sidebar: list[tuple[str, str]] | None,
    *,
    site: str = "Ex",
    nav: list[tuple[str, str]] | None = None,
) -> str:
    menu = ""
    if nav is not None:
        items = "".join(
            f'<a class="VPLink link VPNavBarMenuLink" href="{h}">{t}</a>' for h, t in nav
        )
        menu = (
            f'<nav class="VPNavBarMenu menu">{items}</nav><div class="VPNavBarTranslations">'
            '<a class="VPMenuLink" href="/zh/guide/why">简体中文</a></div>'
        )
    aside = ""
    if sidebar is not None:
        links = "".join(
            f'<div class="VPSidebarItem"><a class="VPLink link" href="{href}">'
            f'<p class="text">{text}</p></a></div>'
            for href, text in sidebar
        )
        aside = f'<aside class="VPSidebar"><nav class="nav">{links}</nav></aside>'
    return f"""<!DOCTYPE html><html lang="en-US"><head>
<meta name="generator" content="VitePress v1.6.4"><title>{title} | {site}</title>
<script id="check-dark-mode">(()=>{{localStorage.getItem("vitepress-theme-appearance")}})();</script>
</head><body><div id="app"><div class="Layout">
<header class="VPNav"><a href="/">Home</a><a href="/api/">API</a>{menu}</header>{aside}
<div class="VPContent has-sidebar" id="VPContent"><div class="VPDoc has-sidebar has-aside">
<div class="container"><div class="aside"><nav class="VPDocAsideOutline">On this page</nav></div>
<div class="content"><div class="content-container"><main class="main">
<div class="vp-doc _guide" style="position:relative;"><div>{body}</div></div></main>
<footer class="VPDocFooter"><div class="edit-info">Edit this page on GitHub</div>
<nav class="prev-next">Previous page Next page</nav></footer></div></div></div></div></div>
<footer class="VPFooter">Released under the MIT License.</footer></div></div></body></html>"""


def _body(heading: str, extra: str = "") -> str:
    return (
        f'<h1 id="x" tabindex="-1">{heading} <a class="header-anchor" href="#x" '
        f'aria-label="Permalink">​</a></h1><p>About {heading}.</p>{extra}'
    )


_SITE = {
    f"{_HOST}/guide/why": _page("Why", _body("Why"), _GUIDE),
    f"{_HOST}/guide/": _page("Getting Started", _body("Getting Started"), _GUIDE),
    f"{_HOST}/guide/features": _page("Features", _body("Features"), _GUIDE),
    f"{_HOST}/reference/config": _page("Config Reference", _body("Config"), _REFERENCE),
    f"{_HOST}/reference/cli": _page("CLI", _body("CLI"), _REFERENCE),
}


def _serve(monkeypatch, pages: dict[str, object], seen: list[str] | None = None) -> list[str]:
    """Serve ``pages``: a string body, an ``(final_url, body)`` redirect, or an exception."""
    seen = seen if seen is not None else []

    def fetch(url, **kwargs):
        seen.append(url)
        if url not in pages:
            raise HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
        page = pages[url]
        if isinstance(page, Exception):
            raise page
        if isinstance(page, tuple):
            return page
        return url, page

    monkeypatch.setattr(http, "fetch_text", fetch)
    return seen


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def _staged(acq) -> list[str]:
    return [p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html"))]


def _sources(acq) -> list[str]:
    return [t.split(" -->", 1)[0].removeprefix("<!-- source: ") for t in _staged(acq)]


# --- the tell ------------------------------------------------------------------------


def test_the_generator_meta_names_vitepress():
    assert _vitepress.is_vitepress(_SITE[f"{_HOST}/guide/why"])


def test_the_default_theme_script_names_vitepress_without_a_generator_meta():
    html = _SITE[f"{_HOST}/guide/why"].replace(
        '<meta name="generator" content="VitePress v1.6.4">', ""
    )
    assert _vitepress.is_vitepress(html)


@pytest.mark.parametrize(
    "html",
    [
        '<html><head><meta name="generator" content="VuePress 2.0.0-rc.9"></head>'
        "<body><div class='vp-page'>VitePress is an alternative.</div></body></html>",
        "<html><body><pre><code>localStorage.getItem('vitepress-theme-appearance')"
        "</code></pre></body></html>",
        '<html><head><meta name="generator" content="Docusaurus v3.5.2"></head>'
        "<body><article>Compare with VitePress.</article></body></html>",
    ],
)
def test_pages_that_only_mention_vitepress_are_not_claimed(html):
    assert not _vitepress.is_vitepress(html)


# --- discovery and order ---------------------------------------------------------------


def test_pages_stage_in_sidebar_order_with_the_entry_at_its_own_position(tmp_path, monkeypatch):
    _serve(monkeypatch, _SITE)
    acq = _vitepress.acquire(f"{_HOST}/guide/", tmp_path, slug="ex", title=None)

    assert _sources(acq) == [
        f"{_HOST}/guide/why",
        f"{_HOST}/guide/",
        f"{_HOST}/guide/features",
        f"{_HOST}/reference/config",
        f"{_HOST}/reference/cli",
    ]
    assert acq.kind == "html"
    assert acq.pages == 5
    assert acq.lost == 0
    assert acq.truncated is False


def test_a_sidebar_reached_from_a_listed_page_joins_the_crawl(tmp_path, monkeypatch):
    """The guide's sidebar links one reference page; that page's own sidebar lists
    the rest of the reference, which follows the guide."""
    _serve(monkeypatch, _SITE)
    acq = _vitepress.acquire(f"{_HOST}/guide/why", tmp_path, slug="ex", title=None)

    assert _sources(acq)[-1] == f"{_HOST}/reference/cli"


def test_a_page_at_a_long_path_stages_under_a_short_file_name(tmp_path, monkeypatch):
    long_path = "/guide/" + "a" * 300  # past a file name's 255-byte limit
    sidebar = [("/guide/", "Getting Started"), (long_path, "Long")]
    _serve(
        monkeypatch,
        {
            f"{_HOST}/guide/": _page("Getting Started", _body("Getting Started"), sidebar),
            f"{_HOST}{long_path}": _page("Long", _body("Long"), sidebar),
        },
    )
    acq = _vitepress.acquire(f"{_HOST}/guide/", tmp_path, slug="ex", title=None)

    assert _sources(acq) == [f"{_HOST}/guide/", f"{_HOST}{long_path}"]
    assert max(len(p.name) for p in acq.raw_dir.iterdir()) <= 100


_NAV = [("/guide/", "Guide"), ("/api/", "API"), ("/blog", "Blog"), ("https://v1.ex.dev/", "v1")]
_API = [("/api/hooks", "Hooks"), ("/api/", "API")]


def _nav_site() -> dict[str, object]:
    pages: dict[str, object] = {
        url: _page(t, _body(t), sb, nav=_NAV)
        for url, t, sb in [
            (f"{_HOST}/guide/why", "Why", _GUIDE),
            (f"{_HOST}/guide/", "Getting Started", _GUIDE),
            (f"{_HOST}/guide/features", "Features", _GUIDE),
            (f"{_HOST}/reference/config", "Config", _REFERENCE),
            (f"{_HOST}/reference/cli", "CLI", _REFERENCE),
            (f"{_HOST}/api/", "API", _API),
            (f"{_HOST}/api/hooks", "Hooks", _API),
            (f"{_HOST}/blog", "Blog", None),
        ]
    }
    return pages


def test_each_top_nav_section_with_a_sidebar_follows_the_entry_section(tmp_path, monkeypatch):
    seen = _serve(monkeypatch, _nav_site())
    acq = _vitepress.acquire(f"{_HOST}/guide/why", tmp_path, slug="ex", title=None)

    assert _sources(acq) == [
        f"{_HOST}/guide/why",
        f"{_HOST}/guide/",
        f"{_HOST}/guide/features",
        f"{_HOST}/reference/config",
        f"{_HOST}/reference/cli",
        f"{_HOST}/api/hooks",
        f"{_HOST}/api/",
    ]
    assert acq.lost == 0
    assert len(seen) == len(set(seen)) == 8
    assert not any("/zh/" in url or "v1.ex.dev" in url for url in seen)


def test_the_page_cap_with_nav_sections_left_truncates(tmp_path, monkeypatch):
    monkeypatch.setattr(_vitepress, "_MAX_PAGES", 5)
    _serve(monkeypatch, _nav_site())
    acq = _vitepress.acquire(f"{_HOST}/guide/why", tmp_path, slug="ex", title=None)

    assert acq.pages == 5
    assert acq.truncated is True


def test_every_page_is_fetched_once(tmp_path, monkeypatch):
    seen = _serve(monkeypatch, _SITE)
    _vitepress.acquire(f"{_HOST}/guide/", tmp_path, slug="ex", title=None)

    assert len(seen) == len(set(seen)) == 5


def test_the_crawl_stays_on_the_host_and_skips_files(tmp_path, monkeypatch):
    sidebar = [
        ("/guide/why", "Why"),
        ("https://github.com/ex/ex/releases", "Changelog"),
        ("/downloads/ex.pdf", "PDF"),
        ("/guide/features#install", "Install"),
    ]
    pages = {
        f"{_HOST}/guide/why": _page("Why", _body("Why"), sidebar),
        f"{_HOST}/guide/features": _page("Features", _body("Features"), sidebar),
    }
    seen = _serve(monkeypatch, pages)
    acq = _vitepress.acquire(f"{_HOST}/guide/why", tmp_path, slug="ex", title=None)

    assert seen == [f"{_HOST}/guide/why", f"{_HOST}/guide/features"]
    assert acq.pages == 2
    assert acq.lost == 0


def test_a_slashless_entry_stages_once_under_its_sidebar_url(tmp_path, monkeypatch):
    """docs_probe strips the seed's slash; the sidebar's form is the page's real
    URL, so relative refs resolve inside the directory."""
    guide = _page("Getting Started", _body("Getting Started", '<a href="./features">f</a>'), _GUIDE)
    pages = dict(_SITE, **{f"{_HOST}/guide": guide, f"{_HOST}/guide/": guide})
    seen = _serve(monkeypatch, pages)
    acq = _vitepress.acquire(f"{_HOST}/guide", tmp_path, slug="ex", title=None)

    assert _sources(acq)[1] == f"{_HOST}/guide/"
    assert acq.pages == 5
    assert f"{_HOST}/guide/" not in seen  # the entry body is reused, not refetched
    assert f'href="{_HOST}/guide/features"' in _staged(acq)[1]


def test_one_page_under_two_urls_stages_once(tmp_path, monkeypatch):
    sidebar = [("/guide/", "Start"), ("/guide/index.html", "Start again"), ("/guide/a", "A")]
    start = _page("Start", _body("Start"), sidebar)
    pages = {
        f"{_HOST}/guide/": start,
        f"{_HOST}/guide/index.html": start,
        f"{_HOST}/guide/a": _page("A", _body("A"), sidebar),
    }
    _serve(monkeypatch, pages)
    acq = _vitepress.acquire(f"{_HOST}/guide/", tmp_path, slug="ex", title=None)

    assert acq.pages == 2
    assert acq.lost == 0


def test_a_redirect_off_the_host_is_dropped(tmp_path, monkeypatch):
    sidebar = [("/guide/why", "Why"), ("/guide/moved", "Moved")]
    pages = {
        f"{_HOST}/guide/why": _page("Why", _body("Why"), sidebar),
        f"{_HOST}/guide/moved": ("https://elsewhere.test/moved", _page("M", _body("M"), None)),
    }
    _serve(monkeypatch, pages)
    acq = _vitepress.acquire(f"{_HOST}/guide/why", tmp_path, slug="ex", title=None)

    assert _sources(acq) == [f"{_HOST}/guide/why"]


def test_a_home_page_without_a_sidebar_follows_its_hero_action(tmp_path, monkeypatch):
    home = (
        '<html><head><meta name="generator" content="VitePress v1.6.4"><title>Ex</title></head>'
        '<body><div class="VPHome"><div class="VPHero"><div class="actions">'
        '<a class="VPButton brand" href="https://github.com/ex/ex">GitHub</a>'
        '<a class="VPButton brand" href="/guide/why">Get Started</a>'
        "</div></div></div></body></html>"
    )
    seen = _serve(monkeypatch, dict(_SITE, **{f"{_HOST}": home}))
    acq = _vitepress.acquire(_HOST, tmp_path, slug="ex", title="Ex")

    assert seen[:2] == [_HOST, f"{_HOST}/guide/why"]
    assert _HOST not in _sources(acq)
    assert acq.pages == 5


def test_an_entry_with_no_sidebar_and_no_hero_is_refused(tmp_path, monkeypatch):
    lone = _page("Team", _body("Team"), None)
    _serve(monkeypatch, {f"{_HOST}/team": lone})
    with pytest.raises(InvalidInputError, match="sidebar"):
        _vitepress.acquire(f"{_HOST}/team", tmp_path, slug="ex", title=None)


# --- content --------------------------------------------------------------------------


_RICH = _body(
    "Rich",
    '<div data-nosnippet hidden style="display:none;">Are you an LLM? Read /x.md</div>'
    '<div class="tip custom-block"><p class="custom-block-title">TIP</p><p>Keep it.</p></div>'
    '<div class="language-ts vp-adaptive-theme"><button title="Copy Code" class="copy"></button>'
    '<span class="lang">ts</span><pre class="shiki vp-code"><code><span class="line">'
    "<span>const a = 1</span></span></code></pre>"
    '<div class="line-numbers-wrapper" aria-hidden="true"><span class="line-number">1</span></div>'
    "</div>"
    '<div class="vp-code-group vp-adaptive-theme"><div class="tabs">'
    '<input type="radio" name="g" id="t1" checked><label data-title="npm" for="t1">npm</label>'
    '<input type="radio" name="g" id="t2"><label data-title="pnpm" for="t2">pnpm</label></div>'
    '<div class="blocks"><div class="language-sh active"><pre><code>npm add ex</code></pre></div>'
    '<div class="language-sh"><pre><code>pnpm add ex</code></pre></div></div></div>'
    '<div class="plugin-tabs"><div class="plugin-tabs--tab-list" role="tablist">'
    '<button id="tab-A-0" class="plugin-tabs--tab">Chromium</button>'
    '<button id="tab-B-0" class="plugin-tabs--tab">Firefox</button></div>'
    '<div class="plugin-tabs--content" aria-labelledby="tab-A-0" id="panel-A-0">'
    "<p>Chromium setup.</p></div></div>"
    '<div class="v-popper twoslash-hover"><span>Reporter</span>'
    '<div class="v-popper__popper twoslash-floating" aria-hidden="true">type Reporter = {}</div>'
    "</div>"
    '<div class="vueschool"><a rel="sponsored noopener" href="https://ads.test/x">'
    "Watch a free video lesson</a></div>"
    '<p><a href="./features">next</a> <img src="/shot.png"> '
    '<img src="/tiny.png" srcset="/small.png 400w, /large.png 1200w"></p>',
)


def _rich(tmp_path, monkeypatch) -> str:
    sidebar = [("/guide/rich", "Rich")]
    _serve(monkeypatch, {f"{_HOST}/guide/rich": _page("Rich", _RICH, sidebar)})
    acq = _vitepress.acquire(f"{_HOST}/guide/rich", tmp_path, slug="ex", title=None)
    return _staged(acq)[0]


@pytest.mark.parametrize(
    "gone",
    [
        "header-anchor",
        "​",
        "Are you an LLM",
        'class="copy"',
        'class="lang"',
        "line-number",
        "type Reporter",
        "Watch a free video lesson",
        "Edit this page",
        "Previous page",
        "On this page",
        "Released under",
        "VPSidebar",
        "VPNav",
        'type="radio"',
        "plugin-tabs--tab-list",
    ],
)
def test_theme_chrome_is_stripped(tmp_path, monkeypatch, gone):
    assert gone not in _rich(tmp_path, monkeypatch)


def test_content_survives_extraction(tmp_path, monkeypatch):
    page = _rich(tmp_path, monkeypatch)

    assert "<h1" in page and "Rich" in page
    assert "custom-block-title" in page and "Keep it." in page
    assert "const a = 1" in page
    assert "Chromium setup." in page
    assert "<span>Reporter</span>" in page


def test_highlighted_code_is_plain_text_under_its_language(tmp_path, monkeypatch):
    page = _rich(tmp_path, monkeypatch)

    assert '<pre><code class="language-ts">const a = 1</code></pre>' in page
    assert "shiki" not in page


def test_each_code_group_block_is_labelled_with_its_tab(tmp_path, monkeypatch):
    page = _rich(tmp_path, monkeypatch)

    assert page.index("npm") < page.index("npm add ex") < page.index("pnpm")
    assert page.index("pnpm") < page.index("pnpm add ex")


def test_a_rendered_tab_panel_is_labelled_with_its_tab(tmp_path, monkeypatch):
    page = _rich(tmp_path, monkeypatch)

    assert page.index("Chromium") < page.index("Chromium setup.")
    assert "Firefox" not in page


def test_refs_are_absolute_and_images_flattened(tmp_path, monkeypatch):
    page = _rich(tmp_path, monkeypatch)

    assert f'href="{_HOST}/guide/features"' in page
    assert f'src="{_HOST}/shot.png"' in page
    assert f'src="{_HOST}/large.png"' in page
    assert "srcset" not in page


# --- losses, caps and stalls ---------------------------------------------------------


def test_a_failed_fetch_and_a_page_without_content_count_as_lost(tmp_path, monkeypatch):
    sidebar = [("/guide/why", "Why"), ("/guide/gone", "Gone"), ("/guide/hollow", "Hollow")]
    hollow = _page("Hollow", "", sidebar).replace('class="vp-doc _guide"', 'class="VPHomeContent"')
    pages = {
        f"{_HOST}/guide/why": _page("Why", _body("Why"), sidebar),
        f"{_HOST}/guide/gone": OSError("reset"),
        f"{_HOST}/guide/hollow": hollow,
    }
    _serve(monkeypatch, pages)
    acq = _vitepress.acquire(f"{_HOST}/guide/why", tmp_path, slug="ex", title=None)

    assert acq.pages == 1
    assert acq.lost == 2


def test_a_same_host_page_another_tool_built_is_skipped_not_lost(tmp_path, monkeypatch):
    sidebar = [("/guide/why", "Why"), ("/api/", "API")]
    pages = {
        f"{_HOST}/guide/why": _page("Why", _body("Why"), sidebar),
        f"{_HOST}/api/": '<html><head><meta name="generator" content="TypeDoc 0.26">'
        "</head><body><main>API</main></body></html>",
    }
    _serve(monkeypatch, pages)
    acq = _vitepress.acquire(f"{_HOST}/guide/why", tmp_path, slug="ex", title=None)

    assert acq.pages == 1
    assert acq.lost == 0


def test_a_site_with_no_content_container_anywhere_is_refused(tmp_path, monkeypatch):
    sidebar = [("/guide/why", "Why")]
    page = _page("Why", _body("Why"), sidebar).replace('class="vp-doc _guide"', 'class="x"')
    _serve(monkeypatch, {f"{_HOST}/guide/why": page})
    with pytest.raises(InvalidInputError, match="vp-doc"):
        _vitepress.acquire(f"{_HOST}/guide/why", tmp_path, slug="ex", title=None)


def test_the_page_cap_truncates(tmp_path, monkeypatch):
    monkeypatch.setattr(_vitepress, "_MAX_PAGES", 2)
    _serve(monkeypatch, _SITE)
    acq = _vitepress.acquire(f"{_HOST}/guide/", tmp_path, slug="ex", title=None)

    assert acq.pages == 2
    assert acq.truncated is True


def test_a_stalled_crawl_stops_and_reports_truncated(tmp_path, monkeypatch):
    sidebar = [(f"/guide/p{i}", str(i)) for i in range(30)]
    same = _page("Same", _body("Same"), sidebar)
    clock = {"t": 0.0}
    monkeypatch.setattr(_vitepress.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(_vitepress.cfg, "CRAWL_STALL_AFTER_S", 30)

    def fetch(url, **kwargs):
        clock["t"] += 5.0
        return url, same

    monkeypatch.setattr(http, "fetch_text", fetch)
    acq = _vitepress.acquire(f"{_HOST}/guide/p0", tmp_path, slug="ex", title=None)

    assert acq.truncated is True
    assert acq.pages == 1


def test_every_request_after_the_first_waits_the_polite_delay(tmp_path, monkeypatch):
    events: list[str] = []
    seen = _serve(monkeypatch, _SITE)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: events.append("sleep"))
    real = http.fetch_text

    def fetch(url, **kwargs):
        events.append("fetch")
        return real(url, **kwargs)

    monkeypatch.setattr(http, "fetch_text", fetch)
    _vitepress.acquire(f"{_HOST}/guide/", tmp_path, slug="ex", title=None)

    assert len(seen) == 5
    fetches = [i for i, e in enumerate(events) if e == "fetch"]
    assert all(events[i - 1] == "sleep" for i in fetches[1:])


# --- title and hand-off ---------------------------------------------------------------


def test_the_title_is_the_site_part_of_the_page_title(tmp_path, monkeypatch):
    _serve(monkeypatch, _SITE)
    acq = _vitepress.acquire(f"{_HOST}/guide/", tmp_path, slug="ex", title="whatever")

    assert acq.title == "Ex"


def test_normalize_merges_the_pages_in_order(tmp_path, monkeypatch):
    _serve(monkeypatch, _SITE)
    acq = _vitepress.acquire(f"{_HOST}/guide/", tmp_path, slug="ex", title=None)
    out = DocsProbePattern().normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert "<title>Ex</title>" in out
    order = [
        "About Why.",
        "About Getting Started.",
        "About Features.",
        "About Config.",
        "About CLI.",
    ]
    positions = [out.index(text) for text in order]
    assert positions == sorted(positions)
