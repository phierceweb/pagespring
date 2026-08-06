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


def test_a_page_without_an_article_counts_as_lost(tmp_path, monkeypatch):
    """A 200 page whose <article> is absent was dropped silently — only fetch
    errors counted, so a theme change audited clean while shipping short."""
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    acq = _docusaurus.acquire("https://ex.io/docs", tmp_path, slug="ex", title="Ex Docs")

    assert acq.lost == 2  # /broken (fetch error) + /no-article (missing container)
    assert acq.pages == 2
    assert not any("no-article" in p.name for p in acq.raw_dir.glob("*.html"))


def test_extract_drops_scripts_and_styles():
    """Docusaurus ships hydration payloads and inline styles inside <article>."""
    html = """<html><body><article>
      <h1>Guide</h1><p>Real content.</p>
      <script>window.__DOCUSAURUS_STATE__={};</script>
      <style>.token{color:#abc}</style>
      <noscript>Enable JS</noscript>
    </article></body></html>"""
    frag = _docusaurus._extract(html, "https://ex.org/docs/guide")
    assert frag is not None
    assert "Real content." in frag
    assert "<script" not in frag and "<style" not in frag and "<noscript" not in frag
    assert "__DOCUSAURUS_STATE__" not in frag
