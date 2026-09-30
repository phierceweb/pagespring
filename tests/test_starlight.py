"""_starlight — sitemap-driven crawl of a Starlight site, scoped to the seed's
locale and read in sidebar order (mocked fetch, no network)."""

import pytest
from bs4 import BeautifulSoup
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.patterns import _starlight
from pagespring.patterns.docs_probe import DocsProbePattern

_H = "https://docs.test"
# Not alphabetical, so sidebar order is told apart from sitemap order.
_SIDEBAR = (
    "/getting-started/",
    "/reference/cli/",
    "/guides/install/",
    "/guides/config/",
    "https://github.com/vendor/repo",
)
_GENERATORS = (
    '<meta name="generator" content="Astro v7.2.10">'
    '<meta name="generator" content="Starlight v0.42.0">'
)


def _page(
    title,
    *,
    path="",
    locales=("", "de"),
    sidebar=_SIDEBAR,
    generator=True,
    container=True,
    sitemap="/sitemap-index.xml",
    hero=False,
):
    rest = path.strip("/")
    alternates = "".join(
        f'<link rel="alternate" hreflang="{loc or "en"}" '
        f'href="{_H}/{loc + "/" if loc else ""}{rest + "/" if rest else ""}">'
        for loc in locales
    )
    head = (
        (_GENERATORS if generator else "")
        + (f'<link rel="sitemap" href="{sitemap}">' if sitemap else "")
        + alternates
        + f"<title>{title} | Vendor</title>"
    )
    nav = "".join(f'<li><a href="{href}">{href}</a></li>' for href in sidebar)
    heading = (
        f'<div class="hero"><div class="copy"><h1 id="_top">{title}</h1>'
        '<div class="tagline">The tagline.</div></div>'
        '<div class="actions"><a href="/getting-started/">Get started</a></div></div>'
        if hero
        else f'<div class="content-panel"><div class="sl-container"><h1 id="_top">{title}</h1>'
        "</div></div>"
    )
    body = (
        '<div class="sl-markdown-content">'
        f'<div class="sl-heading-wrapper level-h2"><h2 id="usage">Usage of {title}</h2>'
        '<a class="sl-anchor-link" href="#usage"><span aria-hidden="true"><svg></svg></span>'
        '<span class="sr-only">Section titled "Usage"</span></a></div>'
        f"<p>Body of {title}.</p><img src='shot.png'></div>"
        if container
        else f"<p>Body of {title}.</p>"
    )
    return (
        f"<html><head>{head}</head><body>"
        f'<nav class="sidebar" aria-label="Main"><div id="starlight__sidebar"><ul>{nav}</ul>'
        "</div></nav>"
        f'<main data-pagefind-body><div class="sl-banner">Site banner</div>{heading}'
        f'<div class="content-panel"><div class="sl-container">{body}'
        '<footer class="sl-flex"><a href="https://github.com/edit">Edit page</a>'
        '<div class="pagination-links">Next</div></footer>'
        '<div class="copyright">Copyright Vendor</div></div></div></main></body></html>'
    )


def _sitemap_of(paths):
    urls = "".join(f"<url><loc>{_H}{p}</loc></url>" for p in paths)
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'


_INDEX = (
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    f"<sitemap><loc>{_H}/sitemap-0.xml</loc></sitemap></sitemapindex>"
)

# Alphabetical, as @astrojs/sitemap writes it.
_ROOT_LOCALE_SITE = (
    "/",
    "/404/",
    "/DE/legacy/",
    "/broken/",
    "/dashboard/",
    "/de/",
    "/de/getting-started/",
    "/de/guides/install/",
    "/getting-started/",
    "/guides/",
    "/guides/config/",
    "/guides/install/",
    "/reference/",
    "/reference/cli/",
    "/reference/rules/no-bar/",
    "/reference/rules/no-foo/",
)


def _site(paths=_ROOT_LOCALE_SITE, **page_kw):
    pages = {f"{_H}/sitemap-index.xml": _INDEX, f"{_H}/sitemap-0.xml": _sitemap_of(paths)}
    for path in paths:
        title = path.strip("/").replace("/", " ") or "home"
        kw = dict(page_kw)
        if path == "/broken/":
            kw["container"] = False
        if path == "/dashboard/":
            pages[f"{_H}{path}"] = "<html><body><main>i18n dashboard app</main></body></html>"
            continue
        if path in ("/", "/de/"):
            kw["hero"] = True
        rest = path
        if path.lower().startswith("/de/"):
            rest = path[3:]
        pages[f"{_H}{path}"] = _page(title, path=rest, **kw)
    return pages


def _serve(monkeypatch, pages, *, redirects=None, seen=None, events=None):
    redirects = redirects or {}

    def fetch(url, **kwargs):
        if seen is not None:
            seen.append(url)
        if events is not None:
            events.append("fetch")
        url = redirects.get(url, url)
        if url not in pages:
            raise OSError(f"404 {url}")
        return url, pages[url]

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(
        http, "polite_sleep", lambda *a, **k: events.append("sleep") if events is not None else None
    )


def _sources(acq):
    return [
        f.read_text(encoding="utf-8").split(" -->", 1)[0].removeprefix("<!-- source: ")
        for f in sorted(acq.raw_dir.glob("*.html"))
    ]


# --- the tell ---------------------------------------------------------------


@pytest.mark.parametrize(
    "html, expected",
    [
        (f"<html><head>{_GENERATORS}</head></html>", True),
        ('<html><head><meta name="generator" content="Starlight v0.15.2"></head></html>', True),
        ('<main><div class="sl-markdown-content"><p>x</p></div></main>', True),
        ('<html><head><meta name="generator" content="Astro v5.1.0"></head></html>', False),
        ('<meta name="generator" content="Docusaurus v3.6.0"><article>x</article>', False),
        ('<meta name="generator" content="VitePress v1.6.3"><main>x</main>', False),
        ("<p>We moved our docs off Starlight last year.</p>", False),
    ],
)
def test_is_starlight(html, expected):
    assert _starlight.is_starlight(html) is expected


# --- scope, locale and order ------------------------------------------------


def test_a_leaf_seed_takes_the_whole_manual_in_its_locale_in_sidebar_order(tmp_path, monkeypatch):
    _serve(monkeypatch, _site())

    acq = _starlight.acquire(f"{_H}/getting-started", tmp_path, slug="vendor", title="T")

    assert _sources(acq) == [
        f"{_H}/",
        f"{_H}/getting-started/",
        f"{_H}/reference/",
        f"{_H}/reference/cli/",
        f"{_H}/reference/rules/no-bar/",
        f"{_H}/reference/rules/no-foo/",
        f"{_H}/guides/",
        f"{_H}/guides/install/",
        f"{_H}/guides/config/",
    ]
    assert acq.pages == 9
    assert acq.lost == 1  # /broken/ is a Starlight page with no markdown body
    assert acq.truncated is False
    assert (acq.kind, acq.slug, acq.title) == ("html", "vendor", "T")


def test_a_section_seed_keeps_only_its_section(tmp_path, monkeypatch):
    _serve(monkeypatch, _site())

    acq = _starlight.acquire(f"{_H}/guides", tmp_path, slug="vendor", title=None)

    assert _sources(acq) == [f"{_H}/guides/", f"{_H}/guides/install/", f"{_H}/guides/config/"]
    assert acq.slug == "vendor-guides"


def test_a_seed_in_another_locale_keeps_that_locale(tmp_path, monkeypatch):
    _serve(monkeypatch, _site())

    acq = _starlight.acquire(f"{_H}/de/getting-started/", tmp_path, slug="vendor", title=None)

    assert _sources(acq) == [
        f"{_H}/de/",
        f"{_H}/de/getting-started/",
        f"{_H}/de/guides/install/",
    ]
    assert acq.slug == "vendor-de"


def test_a_prefixed_default_locale_is_scoped_to_its_prefix(tmp_path, monkeypatch):
    paths = ("/de/getting-started/", "/de/guides/x/", "/en/getting-started/", "/en/guides/x/")
    pages = {f"{_H}/sitemap-index.xml": _INDEX, f"{_H}/sitemap-0.xml": _sitemap_of(paths)}
    for p in paths:
        loc, rest = p.strip("/").split("/", 1)
        pages[f"{_H}{p}"] = _page(
            p, path=f"/{rest}/", locales=("en", "de"), sidebar=(f"/{loc}/getting-started/",)
        )
    _serve(monkeypatch, pages, redirects={f"{_H}/": f"{_H}/en/getting-started/"})

    acq = _starlight.acquire(_H, tmp_path, slug="vendor", title=None)

    assert _sources(acq) == [f"{_H}/en/getting-started/", f"{_H}/en/guides/x/"]
    assert acq.slug == "vendor-en"


def test_without_a_sitemap_the_sidebar_is_the_page_list(tmp_path, monkeypatch):
    pages = {
        f"{_H}{p}": _page(p, path=p, sitemap=None, locales=())
        for p in ("/getting-started/", "/guides/config/", "/guides/install/", "/reference/cli/")
    }
    seen = []
    _serve(monkeypatch, pages, seen=seen)

    acq = _starlight.acquire(f"{_H}/getting-started/", tmp_path, slug="vendor", title=None)

    assert _sources(acq) == [f"{_H}{p}" for p in _SIDEBAR[:4]]
    assert not any("sitemap" in u for u in seen)


def test_a_page_cap_truncates(tmp_path, monkeypatch):
    _serve(monkeypatch, _site())
    monkeypatch.setattr(_starlight, "_MAX_PAGES", 3)

    acq = _starlight.acquire(f"{_H}/getting-started/", tmp_path, slug="vendor", title=None)

    assert acq.pages == 3
    assert acq.truncated is True


def test_a_dead_child_sitemap_reports_truncated(tmp_path, monkeypatch):
    pages = _site()
    pages[f"{_H}/sitemap-index.xml"] = _INDEX.replace(
        "</sitemapindex>", f"<sitemap><loc>{_H}/sitemap-1.xml</loc></sitemap></sitemapindex>"
    )
    _serve(monkeypatch, pages)

    acq = _starlight.acquire(f"{_H}/getting-started/", tmp_path, slug="vendor", title=None)

    assert acq.truncated is True
    assert acq.pages == 9


def test_a_site_whose_pages_all_lack_the_markdown_body_is_refused(tmp_path, monkeypatch):
    _serve(monkeypatch, _site(container=False))

    with pytest.raises(InvalidInputError, match="sl-markdown-content"):
        _starlight.acquire(f"{_H}/getting-started/", tmp_path, slug="vendor", title=None)


def test_every_request_after_the_entry_page_waits_the_polite_delay(tmp_path, monkeypatch):
    events = []
    _serve(monkeypatch, _site(), events=events)

    _starlight.acquire(f"{_H}/getting-started/", tmp_path, slug="vendor", title=None)

    assert events[0] == "fetch"
    assert "fetch, fetch" not in ", ".join(events)


# --- extraction -------------------------------------------------------------


def test_a_page_keeps_its_title_and_markdown_body_and_drops_the_frame(tmp_path, monkeypatch):
    _serve(monkeypatch, _site())

    acq = _starlight.acquire(f"{_H}/guides/", tmp_path, slug="vendor", title=None)
    staged = sorted(acq.raw_dir.glob("*.html"))[1].read_text(encoding="utf-8")
    soup = BeautifulSoup(staged, "html.parser")

    assert soup.find("h1").get_text() == "guides install"
    assert soup.find("h2").get_text() == "Usage of guides install"
    assert "Body of guides install." in soup.get_text()
    for chrome in ("Edit page", "Next", "Copyright", "Site banner", "Section titled"):
        assert chrome not in soup.get_text()
    assert soup.find("a", class_="sl-anchor-link") is None
    assert soup.find("img")["src"] == f"{_H}/guides/install/shot.png"


def test_a_splash_page_keeps_its_heading_and_tagline(tmp_path, monkeypatch):
    _serve(monkeypatch, _site())

    acq = _starlight.acquire(f"{_H}/getting-started/", tmp_path, slug="vendor", title=None)
    home = sorted(acq.raw_dir.glob("*.html"))[0].read_text(encoding="utf-8")
    text = BeautifulSoup(home, "html.parser").get_text(" ", strip=True)

    assert text.startswith("home The tagline.")
    assert "Get started" not in text


def test_normalize_merges_the_pages_in_order(tmp_path, monkeypatch):
    _serve(monkeypatch, _site())
    acq = _starlight.acquire(f"{_H}/guides/", tmp_path, slug="vendor", title="Vendor Docs")

    out = DocsProbePattern().normalize(acq, tmp_path)
    html = out.read_text(encoding="utf-8")

    assert "<title>Vendor Docs</title>" in html
    assert html.index("guides install") < html.index("guides config")


def test_a_grouped_sidebar_adopts_unlisted_pages_only_into_its_groups(tmp_path, monkeypatch):
    """A top-level link outside every sidebar group (a blog, a playground) is site
    navigation: its own page stays, but the pages beneath it are not the manual."""
    import re

    paths = ("/", "/blog/", "/blog/post-a/", "/blog/post-b/", "/guides/install/", "/guides/extra/")
    grouped = (
        '<div id="starlight__sidebar"><ul><li><a href="/blog/">Blog</a></li>'
        "<li><details open><summary>Guides</summary><ul>"
        '<li><a href="/guides/install/">Install</a></li></ul></details></li></ul></div>'
    )
    pages = {
        url: re.sub(r'<div id="starlight__sidebar">.*?</div>', grouped, body, flags=re.S)
        for url, body in _site(paths).items()
    }
    _serve(monkeypatch, pages)

    acq = _starlight.acquire(f"{_H}/guides/install", tmp_path, slug="vendor", title="T")

    assert _sources(acq) == [
        f"{_H}/",
        f"{_H}/blog/",
        f"{_H}/guides/install/",
        f"{_H}/guides/extra/",
    ]
