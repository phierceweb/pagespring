"""_hugo — sitemap-driven crawl for Hugo-built docs sites (mocked fetch).

Hugo emits ``<meta name="generator" content="Hugo x.y.z">`` and a sitemap at
its *site* root — which on a multi-site host is a subdirectory, not the origin,
so the sitemap is discovered by walking up. Content lives in ``<main>`` across
every Hugo docs theme checked (relearn, hugo-book, docsy, gohugo.io's own).
"""

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.patterns import _hugo

_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.example.com/prod/en/index.html</loc></url>
  <url><loc>https://docs.example.com/prod/en/alm/index.html</loc></url>
  <url><loc>https://docs.example.com/prod/en/print/index.html</loc></url>
  <url><loc>https://docs.example.com/other/en/index.html</loc></url>
</urlset>"""

_PAGE = """<html><head><title>Auto-Level</title></head><body>
<header>site header junk</header>
<nav>sidebar nav links</nav>
<main class="main"><article>
<h2>Auto-Level</h2>
<p>Set the target level.</p>
<img src="../images/alm.png">
</article></main>
<footer>site footer junk</footer>
</body></html>"""


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def _fetch(seen, *, sitemap_at):
    """Serve the sitemap only at ``sitemap_at``; 404 every other sitemap path."""

    def fetch(url, **kwargs):
        seen.append(url)
        if url.endswith("sitemap.xml"):
            if url != sitemap_at:
                raise OSError(f"404 {url}")
            return url, _SITEMAP
        return url, _PAGE

    return fetch


def _acquire(tmp_path, monkeypatch, base, *, sitemap_at, seen=None):
    seen = seen if seen is not None else []
    monkeypatch.setattr(http, "fetch_text", _fetch(seen, sitemap_at=sitemap_at))
    return _hugo.acquire(base, tmp_path, slug="prod", title="Prod"), seen


def test_sitemap_at_the_given_depth_is_used(tmp_path, monkeypatch):
    _acq, seen = _acquire(
        tmp_path,
        monkeypatch,
        "https://docs.example.com/prod/en/index.html",
        sitemap_at="https://docs.example.com/prod/en/sitemap.xml",
    )
    assert seen[0] == "https://docs.example.com/prod/en/sitemap.xml"


def test_sitemap_is_discovered_by_walking_up(tmp_path, monkeypatch):
    """gohugo.io keeps its sitemap at the origin while the docs live under /documentation/."""
    _acq, seen = _acquire(
        tmp_path,
        monkeypatch,
        "https://docs.example.com/prod/en/index.html",
        sitemap_at="https://docs.example.com/sitemap.xml",
    )
    sitemaps = [u for u in seen if u.endswith("sitemap.xml")]
    assert sitemaps[-1] == "https://docs.example.com/sitemap.xml"
    assert len(sitemaps) > 1  # walked up rather than guessing the origin outright


def test_print_view_is_excluded(tmp_path, monkeypatch):
    """Hugo's /print/ page concatenates the whole manual — it would duplicate every page."""
    _acq, seen = _acquire(
        tmp_path,
        monkeypatch,
        "https://docs.example.com/prod/en/index.html",
        sitemap_at="https://docs.example.com/prod/en/sitemap.xml",
    )
    assert not any("/print/" in u for u in seen)


def test_only_pages_under_the_base_path_are_kept(tmp_path, monkeypatch):
    """A sitemap at the origin lists sibling products too — pointing at one must not drag in others."""
    acq, seen = _acquire(
        tmp_path,
        monkeypatch,
        "https://docs.example.com/prod/en/index.html",
        sitemap_at="https://docs.example.com/sitemap.xml",
    )
    assert not any("/other/" in u for u in seen)
    assert acq.pages == 2  # prod index + alm; print and other/ dropped


def test_extracts_main_drops_chrome_and_absolutizes(tmp_path, monkeypatch):
    acq, _seen = _acquire(
        tmp_path,
        monkeypatch,
        "https://docs.example.com/prod/en/index.html",
        sitemap_at="https://docs.example.com/prod/en/sitemap.xml",
    )
    joined = "\n".join(p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html")))

    assert "Set the target level." in joined
    assert "sidebar nav links" not in joined
    assert "site header junk" not in joined
    assert "https://docs.example.com/prod/en/images/alm.png" in joined


def test_result_shape(tmp_path, monkeypatch):
    acq, _seen = _acquire(
        tmp_path,
        monkeypatch,
        "https://docs.example.com/prod/en/index.html",
        sitemap_at="https://docs.example.com/prod/en/sitemap.xml",
    )
    assert acq.kind == "html"
    assert acq.slug == "prod"
    assert acq.title == "Prod"


_SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://docs.example.com/prod/en/sitemap.xml</loc></sitemap>
  <sitemap><loc>https://docs.example.com/prod/de/sitemap.xml</loc></sitemap>
</sitemapindex>"""

_CHILD_EN = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.example.com/prod/en/index.html</loc></url>
  <url><loc>https://docs.example.com/prod/en/alm/index.html</loc></url>
</urlset>"""

_CHILD_DE = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.example.com/prod/de/index.html</loc></url>
</urlset>"""


def test_sitemapindex_is_expanded_into_its_child_sitemaps(tmp_path, monkeypatch):
    """Multilingual Hugo sites (relearn, hugo-book, docsy) publish a sitemapindex.
    Its <loc>s are child SITEMAPS, not pages — fetching them as pages collects nothing."""
    seen: list[str] = []

    def fetch(url, **kwargs):
        seen.append(url)
        if url == "https://docs.example.com/prod/sitemap.xml":
            return url, _SITEMAP_INDEX
        if url == "https://docs.example.com/prod/en/sitemap.xml":
            return url, _CHILD_EN
        if url == "https://docs.example.com/prod/de/sitemap.xml":
            return url, _CHILD_DE
        if url.endswith("sitemap.xml"):
            raise OSError(f"404 {url}")
        return url, _PAGE

    monkeypatch.setattr(http, "fetch_text", fetch)

    acq = _hugo.acquire("https://docs.example.com/prod/", tmp_path, slug="prod", title=None)

    assert acq.pages == 3  # 2 from en + 1 from de, all under /prod/
    assert "https://docs.example.com/prod/en/alm/index.html" in seen


def test_no_sitemap_anywhere_is_an_input_error(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fetch([], sitemap_at="https://nowhere/sitemap.xml"))
    with pytest.raises(InvalidInputError):
        _hugo.acquire("https://docs.example.com/prod/en/index.html", tmp_path, slug="p", title=None)


def test_taxonomy_list_pages_are_excluded():
    """Hugo auto-generates /categories/ and /tags/ list pages. They are indexes
    of the manual, not part of it, and each one duplicates the home page."""
    from pagespring.patterns import _hugo

    locs = [
        "https://d.ex.com/ozone/",
        "https://d.ex.com/ozone/eq/",
        "https://d.ex.com/ozone/categories/",
        "https://d.ex.com/ozone/categories/mastering/",
        "https://d.ex.com/ozone/tags/",
        "https://d.ex.com/ozone/tags/eq/",
        "https://d.ex.com/ozone/print/",
    ]
    base = "https://d.ex.com/ozone"
    kept = [u for u in locs if _hugo._is_content_page(u, base)]
    assert kept == ["https://d.ex.com/ozone/", "https://d.ex.com/ozone/eq/"]


def test_a_page_without_main_counts_as_lost(tmp_path, monkeypatch):
    """A 200 page whose <main> is absent was dropped silently — only fetch errors counted."""
    no_main = "<html><body><div id='content'>theme changed</div></body></html>"

    def fetch(url, **kwargs):
        if url.endswith("sitemap.xml"):
            if url != "https://docs.example.com/prod/en/sitemap.xml":
                raise OSError(f"404 {url}")
            return url, _SITEMAP
        if url.endswith("/alm/index.html"):
            return url, no_main
        return url, _PAGE

    monkeypatch.setattr(http, "fetch_text", fetch)

    acq = _hugo.acquire(
        "https://docs.example.com/prod/en/index.html", tmp_path, slug="prod", title=None
    )

    assert acq.lost == 1
    assert acq.pages == 1
    assert not any("alm" in p.name for p in acq.raw_dir.glob("*.html"))


def test_an_unreadable_child_sitemap_truncates_the_result(tmp_path, monkeypatch):
    """Pages behind a failed child sitemap are never discovered, so `lost` cannot
    count them one by one — only truncated can carry the loss."""

    def fetch(url, **kwargs):
        if url == "https://docs.example.com/prod/sitemap.xml":
            return url, _SITEMAP_INDEX
        if url == "https://docs.example.com/prod/en/sitemap.xml":
            return url, _CHILD_EN
        if url == "https://docs.example.com/prod/de/sitemap.xml":
            raise OSError("503 throttled")
        if url.endswith("sitemap.xml"):
            raise OSError(f"404 {url}")
        return url, _PAGE

    monkeypatch.setattr(http, "fetch_text", fetch)

    acq = _hugo.acquire("https://docs.example.com/prod/", tmp_path, slug="prod", title=None)

    assert acq.pages == 2  # only the en child was readable
    assert acq.truncated is True
    assert acq.lost == 0


def test_extract_drops_the_sidebar_nav_drawer():
    """Hugo doc themes render the whole chapter list into every page. It is not
    a <nav>, so the generic chrome selector misses it."""
    from pagespring.patterns import _hugo

    html = """<html><body><main>
      <div class="drawer"><div class="scrollable"><div class="wrapper"><div class="toc">
        <a href="/a/">Introduction</a><a href="/b/">Equalizer</a><a href="/c/">Dither</a>
      </div></div></div></div>
      <h1>Equalizer</h1><p>Real documentation.</p>
    </main></body></html>"""
    frag = _hugo._extract(html, "https://d.ex.com/ozone/eq/")
    assert frag is not None
    assert "Real documentation." in frag
    assert "drawer" not in frag
    assert "Introduction" not in frag and "Dither" not in frag
