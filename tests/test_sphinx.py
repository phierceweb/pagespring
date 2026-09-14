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

_NO_CONTENT_ROOT = """<html><body>
<div id="wrapper"><h1>Research</h1><p>Research body.</p></div>
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


def test_fetch_error_counts_as_lost(tmp_path, monkeypatch):
    """A discovered page whose fetch raises is reported as lost, not silently dropped."""
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    acq = _sphinx.acquire("https://docs.ex.org/en/stable/", tmp_path, slug="ex", title=None)
    assert acq.lost == 1  # broken.html
    assert acq.pages == 3
    assert not any("broken" in f.name for f in acq.raw_dir.glob("*.html"))


def test_page_without_content_root_counts_as_lost(tmp_path, monkeypatch):
    """A 200 page whose content root is absent is reported as lost, same as a fetch error."""

    def fake_fetch(url, **kwargs):
        table = {
            "https://docs.ex.org/en/stable/": _INDEX,
            "https://docs.ex.org/en/stable/usage.html": _USAGE,
            "https://docs.ex.org/en/stable/research.html": _NO_CONTENT_ROOT,
            "https://docs.ex.org/en/stable/broken.html": _RESEARCH,
        }
        return url, table[url]

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    acq = _sphinx.acquire("https://docs.ex.org/en/stable/", tmp_path, slug="ex", title=None)
    assert acq.lost == 1  # research.html; every other page fetches and extracts
    assert acq.pages == 3
    assert not any("research" in f.name for f in acq.raw_dir.glob("*.html"))


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


def test_extract_drops_scripts_and_styles():
    """Sphinx themes inject analytics and inline theme CSS into the content root."""
    html = """<html><body><div role="main">
      <h1>Page</h1><p>Real content.</p>
      <script src="https://analytics.example/track.js"></script>
      <script>var readthedocs = 1;</script>
      <style>.rst-content{color:red}</style>
      <noscript>Enable JS</noscript>
    </div></body></html>"""
    frag = _sphinx._extract(html, "https://docs.ex.org/en/stable/page.html")
    assert frag is not None
    assert "Real content." in frag
    assert "<script" not in frag and "<style" not in frag and "<noscript" not in frag
    assert "analytics.example" not in frag


def test_a_page_reachable_twice_is_staged_once(tmp_path, monkeypatch):
    """Sphinx themes link the start page as `index.html` from every breadcrumb, so a
    directory-URL crawl meets its own entry page under a second name; staging both
    duplicates the body and overstates `pages`."""
    home = """<html><body><div role="main">
      <h1>Welcome</h1><p>Index body.</p>
      <a href="index.html">Home</a>
      <a href="usage.html">Usage</a>
    </div></body></html>"""
    usage = """<html><body><div role="main">
      <h1>Usage</h1><p>Usage body.</p>
    </div></body></html>"""

    def fake_fetch(url, **kwargs):
        table = {
            "https://docs.ex.org/en/stable/": home,
            "https://docs.ex.org/en/stable/index.html": home,
            "https://docs.ex.org/en/stable/usage.html": usage,
        }
        return url, table[url]

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    acq = _sphinx.acquire("https://docs.ex.org/en/stable/", tmp_path, slug="ex", title=None)

    merged = "".join(p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html")))
    assert merged.count("Index body.") == 1, "entry page staged twice under a second URL"
    assert merged.count("Usage body.") == 1
    assert acq.pages == 2, "pages counted the duplicate"


_ROOT = "https://docs.ex.org/en/stable/"


def _page(main="", sidebar=""):
    return f'<html><body><div role="main"><h1>x</h1>{main}</div>{sidebar}</body></html>'


def _link(path):
    return f'<a href="/en/stable/{path}">{path}</a>'


def _toc(*entries, level=1):
    """A rendered toctree list; an entry is a path or (path, *child entries)."""
    items = []
    for entry in entries:
        path, *kids = (entry,) if isinstance(entry, str) else entry
        nested = _toc(*kids, level=level + 1) if kids else ""
        items.append(f'<li class="toctree-l{level}">{_link(path)}{nested}</li>')
    return f"<ul>{''.join(items)}</ul>"


def _sidebar(*entries):
    return f'<div class="sphinxsidebar">{_toc(*entries)}</div>'


def _crawl(tmp_path, monkeypatch, site):
    def fetch(url, **kwargs):
        path = url[len(_ROOT) :]
        return url, site[path].replace("<h1>x</h1>", f"<h1>{path or 'home'}</h1>", 1)

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    acq = _sphinx.acquire(_ROOT, tmp_path, slug="ex", title=None)
    order = []
    for f in sorted(acq.raw_dir.glob("*.html")):
        first = f.read_text(encoding="utf-8").splitlines()[0]
        order.append(first.removeprefix(f"<!-- source: {_ROOT}").removesuffix(" -->"))
    return acq, order


def test_a_child_page_stages_before_the_next_chapter(tmp_path, monkeypatch):
    site = {
        "": _page(_link("ch1.html") + _link("ch2.html")),
        "ch1.html": _page(_link("ch1/s1.html")),
        "ch2.html": _page(),
        "ch1/s1.html": _page(),
    }
    _acq, order = _crawl(tmp_path, monkeypatch, site)
    assert order == ["", "ch1.html", "ch1/s1.html", "ch2.html"]


def test_toctree_order_beats_link_order(tmp_path, monkeypatch):
    """A prose cross-reference ahead of the toctree must not pull a later chapter forward."""
    site = {
        "": _page(
            _link("ch2.html") + f'<div class="toctree-wrapper">{_toc("ch1.html", "ch2.html")}</div>'
        ),
        "ch1.html": _page(f'<div class="toctree-wrapper">{_toc("ch1/s1.html")}</div>'),
        "ch2.html": _page(),
        "ch1/s1.html": _page(),
    }
    _acq, order = _crawl(tmp_path, monkeypatch, site)
    assert order == ["", "ch1.html", "ch1/s1.html", "ch2.html"]


def test_sidebar_toctree_nests_each_page_under_its_chapter(tmp_path, monkeypatch):
    """The theme renders the global toctree outside the content root, expanding only
    the current chapter; each page's sidebar supplies its own branch."""
    ch1_open = _sidebar(("ch1.html", "ch1/a.html"), "ch2.html")
    ch2_open = _sidebar("ch1.html", ("ch2.html", "ch2/b.html"))
    site = {
        "": _page(_link("ch2.html"), _sidebar("ch1.html", "ch2.html")),
        "ch1.html": _page("", ch1_open),
        "ch1/a.html": _page("", ch1_open),
        "ch2.html": _page(_link("ch1/a.html"), ch2_open),
        "ch2/b.html": _page("", ch2_open),
    }
    _acq, order = _crawl(tmp_path, monkeypatch, site)
    assert order == ["", "ch1.html", "ch1/a.html", "ch2.html", "ch2/b.html"]


def test_a_section_sidebar_nests_under_the_section_rendering_it(tmp_path, monkeypatch):
    """Some themes render only the current section's toctree in the sidebar, so its
    top level belongs to that section, not to the site root."""
    guide = _sidebar("guide/a.html", "guide/b.html")
    api = _sidebar("api/x.html")
    site = {
        "": _page(_link("guide.html") + _link("api.html")),
        "guide.html": _page(_link("api/x.html"), guide),
        "guide/a.html": _page("", guide),
        "guide/b.html": _page("", guide),
        "api.html": _page("", api),
        "api/x.html": _page("", api),
    }
    _acq, order = _crawl(tmp_path, monkeypatch, site)
    assert order == ["", "guide.html", "guide/a.html", "guide/b.html", "api.html", "api/x.html"]


def test_a_capped_crawl_stages_up_to_the_cap_and_is_truncated(tmp_path, monkeypatch):
    site = {
        "": _page(_link("ch1.html") + _link("ch2.html")),
        "ch1.html": _page(_link("ch1/s1.html")),
        "ch2.html": _page(),
        "ch1/s1.html": _page(),
    }
    monkeypatch.setattr(_sphinx, "_MAX_PAGES", 2)
    acq, order = _crawl(tmp_path, monkeypatch, site)
    assert acq.pages == 2 and acq.truncated
    assert order == ["", "ch1.html"]


def test_conflicting_toctrees_still_stage_every_page_once(tmp_path, monkeypatch):
    site = {
        "": _page(f'<div class="toctree-wrapper">{_toc("a.html", "b.html", "c.html")}</div>'),
        "a.html": _page(),
        "b.html": _page(),
        "c.html": _page("", _sidebar("b.html", "a.html")),
    }
    acq, order = _crawl(tmp_path, monkeypatch, site)
    assert sorted(order) == ["", "a.html", "b.html", "c.html"]
    assert order[0] == "" and acq.pages == 4


def test_a_capped_crawl_covers_the_top_level_before_one_deep_chain(tmp_path, monkeypatch):
    """Discovery is breadth-first so a cap keeps the manual's chapters, not one
    cross-reference chain; staging still follows reading order."""
    site = {
        "": _page(_link("ch1.html") + _link("ch2.html") + _link("ch3.html")),
        "ch1.html": _page(_link("deep/a.html")),
        "deep/a.html": _page(_link("deep/b.html")),
        "deep/b.html": _page(_link("deep/c.html")),
        "deep/c.html": _page(),
        "ch2.html": _page(),
        "ch3.html": _page(),
    }
    monkeypatch.setattr(_sphinx, "_MAX_PAGES", 4)
    acq, order = _crawl(tmp_path, monkeypatch, site)
    assert acq.truncated
    assert sorted(order) == ["", "ch1.html", "ch2.html", "ch3.html"]
