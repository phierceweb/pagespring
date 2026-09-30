"""_sitemap — sitemap reading, main-content extraction and the page fetch loop
shared by the sitemap-driven crawls (mocked fetch, no network)."""

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.patterns import _readable, _sitemap

_URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:xhtml="http://www.w3.org/1999/xhtml">
  <url><loc>https://d.test/b/</loc>
    <xhtml:link rel="alternate" hreflang="de" href="https://d.test/de/b/"/></url>
  <url><loc> https://d.test/a/ </loc></url>
</urlset>"""

_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://d.test/sitemap-0.xml</loc></sitemap>
  <sitemap><loc>https://d.test/sitemap-1.xml</loc></sitemap>
  <sitemap><loc>https://d.test/sitemap-2.xml</loc></sitemap>
</sitemapindex>"""


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def _serve(monkeypatch, pages, seen=None):
    def fetch(url, **kwargs):
        if seen is not None:
            seen.append(url)
        if url not in pages:
            raise OSError(f"404 {url}")
        body = pages[url]
        if isinstance(body, tuple):
            return body
        return url, body

    monkeypatch.setattr(http, "fetch_text", fetch)


# --- reading sitemaps -------------------------------------------------------


def test_a_urlset_yields_its_page_locs_in_order_without_the_alternates(monkeypatch):
    _serve(monkeypatch, {"https://d.test/sitemap.xml": _URLSET})

    final, locs, child_failed = _sitemap.read_locs("https://d.test/sitemap.xml")

    assert final == "https://d.test/sitemap.xml"
    assert locs == ["https://d.test/b/", "https://d.test/a/"]
    assert child_failed is False


def test_an_index_is_expanded_into_its_children_and_a_dead_child_is_flagged(monkeypatch):
    child = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{}</loc></url></urlset>'
    _serve(
        monkeypatch,
        {
            "https://d.test/sitemap-index.xml": _INDEX,
            "https://d.test/sitemap-0.xml": child.format("https://d.test/one/"),
            "https://d.test/sitemap-2.xml": child.format("https://d.test/two/"),
        },
    )

    _final, locs, child_failed = _sitemap.read_locs("https://d.test/sitemap-index.xml")

    assert locs == ["https://d.test/one/", "https://d.test/two/"]
    assert child_failed is True


def test_a_body_that_is_not_a_sitemap_is_an_input_error(monkeypatch):
    _serve(monkeypatch, {"https://d.test/sitemap.xml": "<html><body>soft 404</body></html>"})

    with pytest.raises(InvalidInputError, match="not a sitemap"):
        _sitemap.read_locs("https://d.test/sitemap.xml")


def test_the_sitemap_a_page_declares_is_resolved_against_the_page():
    html = '<html><head><link rel="sitemap" href="/docs/sitemap-index.xml"></head></html>'

    assert (
        _sitemap.sitemap_link(html, "https://d.test/docs/guide/")
        == "https://d.test/docs/sitemap-index.xml"
    )
    assert _sitemap.sitemap_link("<html><head></head></html>", "https://d.test/") is None


# --- scope ------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, base, expected",
    [
        ("https://d.test/guide/", "https://d.test/guide/", True),
        ("https://d.test/guide/setup/", "https://d.test/guide/", True),
        ("https://D.TEST/guide/setup", "https://d.test/guide/", True),
        ("https://d.test/guide-advanced/", "https://d.test/guide/", False),
        ("https://other.test/guide/setup/", "https://d.test/guide/", False),
        ("https://d.test/anything/", "https://d.test/", True),
    ],
)
def test_under_is_a_path_segment_prefix_on_one_host(url, base, expected):
    assert _sitemap.under(url, base) is expected


def test_directory_drops_a_file_name_and_keeps_a_directory():
    assert _sitemap.directory("https://d.test/docs/sitemap.xml") == "https://d.test/docs/"
    assert _sitemap.directory("https://d.test/docs/v2.1") == "https://d.test/docs/v2.1/"
    assert _sitemap.directory("https://d.test") == "https://d.test/"


def test_error_pages_are_recognized_by_their_last_segment():
    assert _sitemap.is_error_page("https://d.test/zh-CN/404/")
    assert _sitemap.is_error_page("https://d.test/404.html")
    assert not _sitemap.is_error_page("https://d.test/reference/errors/invalid-rewrite404/")


# --- the fetch loop ---------------------------------------------------------


def _page(text: str) -> str:
    return f"<html><body><main><h1>{text}</h1><p>{text} body</p></main></body></html>"


def test_crawl_stages_pages_in_order_and_counts_what_it_loses(tmp_path, monkeypatch):
    _serve(
        monkeypatch,
        {
            "https://d.test/a/": _page("A"),
            "https://d.test/b/": _page("B"),
            "https://d.test/empty/": "<html><body><p>no container</p></body></html>",
            "https://d.test/dupe/": _page("A"),
            "https://d.test/moved/": ("https://elsewhere.test/moved/", _page("M")),
            "https://d.test/foreign/": "<html><body><main>app shell</main></body></html>",
        },
    )
    urls = [
        "https://d.test/a/",
        "https://d.test/gone/",
        "https://d.test/empty/",
        "https://d.test/dupe/",
        "https://d.test/moved/",
        "https://d.test/foreign/",
        "https://d.test/b/",
    ]

    def extract(html, url):
        return _readable.extract_main(html, url) if "<main>" in html and "h1" in html else None

    result = _sitemap.crawl(
        urls,
        tmp_path / "raw",
        extract=extract,
        in_scope=lambda u: _sitemap.under(u, "https://d.test/"),
        belongs=lambda html: "app shell" not in html,
        event="t",
    )

    files = sorted((tmp_path / "raw").glob("*.html"))
    assert [f.name for f in files] == ["0000-a.html", "0001-b.html"]
    assert files[0].read_text(encoding="utf-8").startswith("<!-- source: https://d.test/a/ -->")
    # gone (fetch error) + empty (no container) are lost; the duplicate, the page
    # that redirected off the site and the page the site did not build are not.
    assert (result.saved, result.lost, result.stalled) == (2, 2, False)
    assert result.first_page == _page("A")


def test_crawl_waits_the_polite_delay_before_every_fetch(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: events.append("sleep"))

    def fetch(url, **kwargs):
        events.append("fetch")
        return url, _page(url[-2])

    monkeypatch.setattr(http, "fetch_text", fetch)

    _sitemap.crawl(
        ["https://d.test/a/", "https://d.test/b/"],
        tmp_path / "raw",
        extract=_readable.extract_main,
        in_scope=lambda u: True,
        event="t",
    )

    assert events == ["sleep", "fetch", "sleep", "fetch"]


def test_a_crawl_that_stops_producing_is_cut_short(tmp_path, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(_sitemap.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(_sitemap.cfg, "CRAWL_STALL_AFTER_S", 30)

    def fetch(url, **kwargs):
        clock["t"] += 5.0
        return url, _page("same")

    monkeypatch.setattr(http, "fetch_text", fetch)
    urls = [f"https://d.test/p{i}/" for i in range(40)]

    result = _sitemap.crawl(
        urls,
        tmp_path / "raw",
        extract=_readable.extract_main,
        in_scope=lambda u: True,
        event="t",
    )

    assert result.stalled is True
    assert result.saved == 1
