"""_sphinx — BFS crawl + content-root extraction with synthetic pages (no network)."""

from pagespring import http
from pagespring.patterns import _sphinx

_INDEX = """<html><body>
<div role="main">
  <h1>Welcome</h1><p>Index body.</p>
  <a class="headerlink" href="#welcome">¶</a>
  <a href="usage.html">Usage</a>
  <a href="research.html">Research</a>
  <a href="broken.html">x</a>
  <a href="genindex.html">Index</a>
  <a href="search.html">Search</a>
  <a href="_static/style.css">asset</a>
  <a href="/other/outside.html">outside prefix</a>
  <a href="https://elsewhere.com/x.html">other host</a>
</body></html>"""

_USAGE = """<html><body>
<div role="main">
  <h1>Usage</h1><p>Usage body.</p>
  <img src="../_images/shot.png">
  <a href="usage.html#anchor">self</a>
</div>
</body></html>"""

_RESEARCH = """<html><body>
<div role="main">
  <h1>Research</h1><p>Research body.</p>
</div>
</body></html>"""


def _fake_fetch_text(url, **kwargs):
    if url == "https://docs.ex.org/en/stable/broken.html":
        raise RuntimeError("boom")
    table = {
        "https://docs.ex.org/en/stable/": _INDEX,
        "https://docs.ex.org/en/stable/usage.html": _USAGE,
        "https://docs.ex.org/en/stable/research.html": _RESEARCH,
    }
    return url, table[url]


def test_acquire_crawls_prefix_and_extracts_main(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    acq = _sphinx.acquire("https://docs.ex.org/en/stable/", tmp_path, slug="ex", title="Ex Docs")
    assert acq.kind == "html"
    assert acq.pages == 3  # index + usage + research; genindex/search/_static/outside skipped

    files = sorted(acq.raw_dir.glob("*.html"))
    assert len(files) == 3
    index = files[0].read_text(encoding="utf-8")
    assert "<h1>Welcome</h1>" in index
    assert "headerlink" not in index  # ¶ anchors stripped
    usage = files[1].read_text(encoding="utf-8")
    assert "<h1>Usage</h1>" in usage
    # Relative image absolutized against the page URL.
    assert 'src="https://docs.ex.org/en/_images/shot.png"' in usage


def test_skip_matching_is_precise_not_substring(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    acq = _sphinx.acquire("https://docs.ex.org/en/stable/", tmp_path, slug="ex", title=None)
    # research.html crawled ("search" is only a substring); genindex/search still skipped.
    assert acq.pages == 3
    saved = [f.read_text(encoding="utf-8") for f in acq.raw_dir.glob("*.html")]
    assert any("<h1>Research</h1>" in text for text in saved)


def test_fetch_failure_sleeps_before_continuing(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: sleeps.append(1))
    acq = _sphinx.acquire("https://docs.ex.org/en/stable/", tmp_path, slug="ex", title=None)
    assert acq.pages == 3  # broken.html failed to fetch — not saved
    # One polite sleep per dequeued URL: index, usage, research, and the broken fetch.
    assert len(sleeps) == 4


def test_file_suffixed_start_url_crawls_its_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    acq = _sphinx.acquire(
        "https://docs.ex.org/en/stable/index.html", tmp_path, slug="ex", title=None
    )
    assert acq.pages == 3  # same crawl as starting from the directory URL


def test_bare_host_start_url_crawls_site_root(tmp_path, monkeypatch):
    root = """<html><body>
<div role="main"><h1>Root</h1><p>Root body.</p></div>
</body></html>"""

    def fake_fetch(url, **kwargs):
        return url, {"https://docs.ex.org/": root}[url]

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    acq = _sphinx.acquire("https://docs.ex.org", tmp_path, slug="ex", title=None)
    assert acq.pages == 1
    (saved,) = acq.raw_dir.glob("*.html")
    assert "source: https://docs.ex.org/" in saved.read_text(encoding="utf-8")


def test_dotted_version_dir_start_url_stays_in_dir(tmp_path, monkeypatch):
    index = """<html><body>
<div role="main"><h1>V3.11</h1><a href="usage.html">Usage</a></div>
</body></html>"""
    usage = """<html><body>
<div role="main"><h1>Usage</h1></div>
</body></html>"""
    old = """<html><body>
<div role="main"><h1>Old</h1></div>
</body></html>"""

    def fake_fetch(url, **kwargs):
        table = {
            "https://docs.ex.org/3.11/": index,
            "https://docs.ex.org/3.11/usage.html": usage,
            "https://docs.ex.org/2.7/old.html": old,  # reachable only if the crawl escapes
        }
        return url, table[url]

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    acq = _sphinx.acquire("https://docs.ex.org/3.11", tmp_path, slug="ex", title=None)
    assert acq.pages == 2  # confined to /3.11/ — a dotted version dir is not a file suffix
    for f in sorted(acq.raw_dir.glob("*.html")):
        first_line = f.read_text(encoding="utf-8").splitlines()[0]
        assert first_line.startswith("<!-- source: https://docs.ex.org/3.11/")
