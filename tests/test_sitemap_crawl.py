"""sitemap_crawl — opt-in crawl of the pages a named sitemap lists (mocked fetch)."""

import pytest
from bs4 import BeautifulSoup
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.base import Pattern
from pagespring.patterns.sitemap_crawl import SitemapCrawlPattern

_H = "https://vendor.test"


def _page(title, *, site_name="Vendor"):
    meta = f'<meta property="og:site_name" content="{site_name}">' if site_name else ""
    return (
        f"<html><head>{meta}<title>{title} | Vendor</title></head><body>"
        "<header>Site header</header><nav>Global nav</nav>"
        f"<main><nav aria-label='breadcrumbs'>Docs / {title}</nav>"
        f"<article><h1>{title}</h1><p>{'Body of ' + title + '. ' * 10}</p>"
        "<img src='img/shot.png'></article>"
        "<footer>Was this page helpful?</footer></main>"
        "<footer>Site footer</footer></body></html>"
    )


def _urlset(paths):
    urls = "".join(f"<url><loc>{_H}{p}</loc></url>" for p in paths)
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'


_PATHS = (
    "/docs/",
    "/docs/intro/",
    "/blog/post/",
    "/docs/404/",
    "/docs/broken/",
    "/docs/empty/",
    "/docs/guide/setup/",
    "/docs/guide/setup/",
)


def _site(paths=_PATHS, sitemap="/docs/sitemap.xml"):
    pages = {f"{_H}{sitemap}": _urlset(paths)}
    for p in paths:
        if p == "/docs/broken/":
            continue
        if p == "/docs/empty/":
            pages[f"{_H}{p}"] = "<html><body><img src='x.png'></body></html>"
            continue
        pages[f"{_H}{p}"] = _page(p.strip("/").replace("/", " "))
    return pages


def _serve(monkeypatch, pages, *, redirects=None, seen=None):
    redirects = redirects or {}

    def fetch(url, **kwargs):
        if seen is not None:
            seen.append(url)
        url = redirects.get(url, url)
        if url not in pages:
            raise OSError(f"404 {url}")
        return url, pages[url]

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def _sources(acq):
    return [
        f.read_text(encoding="utf-8").split(" -->", 1)[0].removeprefix("<!-- source: ")
        for f in sorted(acq.raw_dir.glob("*.html"))
    ]


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://vendor.test/sitemap.xml", True),
        ("https://vendor.test/docs/sitemap-index.xml", True),
        ("https://vendor.test/sitemap_index.xml", True),
        ("https://vendor.test/sitemap-0.xml", True),
        ("https://vendor.test/SITEMAP.XML?lang=en", True),
        ("https://vendor.test/sitemap/", False),
        ("https://vendor.test/sitemap.html", False),
        ("https://vendor.test/feed.xml", False),
        ("https://vendor.test/docs/sitemaps-guide/", False),
        ("file:///tmp/sitemap.xml", False),
    ],
)
def test_match_claims_only_a_url_naming_a_sitemap(url, expected):
    assert SitemapCrawlPattern().match(url) is expected


def test_it_satisfies_the_pattern_protocol():
    assert isinstance(SitemapCrawlPattern(), Pattern)
    assert SitemapCrawlPattern.name == "sitemap_crawl"


def test_acquire_crawls_the_pages_under_the_sitemaps_directory_in_order(tmp_path, monkeypatch):
    seen = []
    _serve(monkeypatch, _site(), seen=seen)

    acq = SitemapCrawlPattern().acquire(f"{_H}/docs/sitemap.xml", tmp_path)

    assert _sources(acq) == [f"{_H}/docs/", f"{_H}/docs/intro/", f"{_H}/docs/guide/setup/"]
    assert f"{_H}/blog/post/" not in seen
    assert f"{_H}/docs/404/" not in seen
    assert seen.count(f"{_H}/docs/guide/setup/") == 1
    assert (acq.pages, acq.lost, acq.truncated) == (3, 2, False)
    assert (acq.kind, acq.slug, acq.title) == ("html", "vendor-docs", "Vendor")


def test_the_main_content_is_kept_and_the_chrome_dropped(tmp_path, monkeypatch):
    _serve(monkeypatch, _site())

    acq = SitemapCrawlPattern().acquire(f"{_H}/docs/sitemap.xml", tmp_path)
    staged = sorted(acq.raw_dir.glob("*.html"))[1].read_text(encoding="utf-8")
    soup = BeautifulSoup(staged, "html.parser")

    assert soup.find("h1").get_text() == "docs intro"
    for chrome in ("Site header", "Global nav", "breadcrumbs", "helpful", "Site footer"):
        assert chrome not in staged
    assert soup.find("img")["src"] == f"{_H}/docs/intro/img/shot.png"


def test_a_redirected_sitemap_scopes_to_where_it_landed(tmp_path, monkeypatch):
    pages = _site(sitemap="/v2/docs/sitemap.xml", paths=("/v2/docs/a/", "/docs/b/"))
    _serve(monkeypatch, pages, redirects={f"{_H}/docs/sitemap.xml": f"{_H}/v2/docs/sitemap.xml"})

    acq = SitemapCrawlPattern().acquire(f"{_H}/docs/sitemap.xml", tmp_path)

    assert _sources(acq) == [f"{_H}/v2/docs/a/"]
    assert acq.slug == "vendor-v2-docs"


def test_a_root_sitemap_takes_the_whole_site_and_names_it_by_host(tmp_path, monkeypatch):
    _serve(monkeypatch, _site(sitemap="/sitemap.xml"))

    acq = SitemapCrawlPattern().acquire(f"{_H}/sitemap.xml", tmp_path)

    assert f"{_H}/blog/post/" in _sources(acq)
    assert acq.slug == "vendor"


def test_the_title_falls_back_to_none_without_a_site_name(tmp_path, monkeypatch):
    pages = _site()
    pages = {
        u: b.replace('<meta property="og:site_name" content="Vendor">', "")
        for u, b in pages.items()
    }
    _serve(monkeypatch, pages)

    acq = SitemapCrawlPattern().acquire(f"{_H}/docs/sitemap.xml", tmp_path)

    assert acq.title is None


def test_a_page_cap_truncates(tmp_path, monkeypatch):
    from pagespring.patterns import sitemap_crawl

    _serve(monkeypatch, _site())
    monkeypatch.setattr(sitemap_crawl, "_MAX_PAGES", 2)

    acq = SitemapCrawlPattern().acquire(f"{_H}/docs/sitemap.xml", tmp_path)

    assert acq.pages == 2
    assert acq.truncated is True


def test_a_sitemap_listing_nothing_under_its_directory_is_refused(tmp_path, monkeypatch):
    _serve(monkeypatch, _site(paths=("/blog/post/",)))

    with pytest.raises(InvalidInputError, match="no page under"):
        SitemapCrawlPattern().acquire(f"{_H}/docs/sitemap.xml", tmp_path)


def test_a_sitemap_whose_pages_all_lack_content_is_refused(tmp_path, monkeypatch):
    _serve(monkeypatch, _site(paths=("/docs/empty/", "/docs/broken/")))

    with pytest.raises(InvalidInputError, match="content"):
        SitemapCrawlPattern().acquire(f"{_H}/docs/sitemap.xml", tmp_path)


def test_normalize_merges_the_pages_into_one_titled_file(tmp_path, monkeypatch):
    _serve(monkeypatch, _site())
    pattern = SitemapCrawlPattern()
    acq = pattern.acquire(f"{_H}/docs/sitemap.xml", tmp_path)

    out = pattern.normalize(acq, tmp_path)
    html = out.read_text(encoding="utf-8")

    assert out.name == "vendor-docs.html"
    assert "<title>Vendor</title>" in html
    assert html.index("docs intro") < html.index("docs guide setup")


def test_normalize_over_an_empty_raw_dir_writes_an_empty_file(tmp_path):
    from pagespring.base import AcquireResult

    raw = tmp_path / "raw"
    raw.mkdir()
    acq = AcquireResult(raw_dir=raw, kind="html", slug="s", pages=0)

    out = SitemapCrawlPattern().normalize(acq, tmp_path)

    assert out.read_text(encoding="utf-8") == ""
