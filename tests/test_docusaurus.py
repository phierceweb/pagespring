"""_docusaurus — sitemap-filtered crawl with synthetic pages (no network)."""

from pagespring import http
from pagespring.patterns import _docusaurus

_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://ex.io/docs/intro</loc></url>
  <url><loc>https://ex.io/docs/guide/setup</loc></url>
  <url><loc>https://ex.io/docs/2.4.1/intro</loc></url>
  <url><loc>https://ex.io/docs/2.x/intro</loc></url>
  <url><loc>https://ex.io/docs/next/intro</loc></url>
  <url><loc>https://ex.io/blog/release-3</loc></url>
  <url><loc>https://ex.io/docs/broken</loc></url>
  <url><loc>https://ex.io/docs/no-article</loc></url>
</urlset>
"""

_PAGE = (
    "<html><body><div id='__docusaurus'><article>"
    "<nav class='theme-doc-breadcrumbs'>Home &gt; Intro</nav>"
    "<h1>{title}</h1><p>Body of {title}.</p>"
    "<img src='/img/shot.png'>"
    "<a href='/docs/guide/setup'>next page</a>"
    "<nav class='pagination-nav'>Previous / Next</nav>"
    "</article></body></html>"
)


_NO_ARTICLE_PAGE = (
    "<html><body><div id='__docusaurus'><p>No article tag here.</p></div></body></html>"
)


def _fake_fetch_text(url, **kwargs):
    if url.endswith("sitemap.xml"):
        return url, _SITEMAP
    if url.endswith("/broken"):
        raise RuntimeError("boom")
    if url.endswith("/no-article"):
        return url, _NO_ARTICLE_PAGE
    title = url.rsplit("/", 1)[-1]
    return url, _PAGE.format(title=title)


def test_acquire_filters_versions_and_strips_chrome(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    acq = _docusaurus.acquire("https://ex.io/docs", tmp_path, slug="ex", title="Ex Docs")
    assert acq.kind == "html"
    # intro + guide/setup + broken + no-article; 2.4.1 + 2.x + next + blog dropped.
    # Only intro + guide/setup are saved — broken/no-article are skip-and-continue.
    assert acq.pages == 2

    files = sorted(acq.raw_dir.glob("*.html"))
    assert len(files) == 2
    intro = files[0].read_text(encoding="utf-8")
    assert "<h1>intro</h1>" in intro
    assert "source: https://ex.io/docs/intro" in intro
    # Chrome gone.
    assert "breadcrumbs" not in intro and "pagination" not in intro
    # Refs absolutized.
    assert 'src="https://ex.io/img/shot.png"' in intro
    assert 'href="https://ex.io/docs/guide/setup"' in intro


def test_fetch_failure_and_missing_article_sleep_before_continuing(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: sleeps.append(1))
    acq = _docusaurus.acquire("https://ex.io/docs", tmp_path, slug="ex", title="Ex Docs")
    assert acq.pages == 2  # broken + no-article skipped, not saved
    # One polite sleep per crawled URL: intro, guide/setup, broken, no-article.
    assert len(sleeps) == 4
