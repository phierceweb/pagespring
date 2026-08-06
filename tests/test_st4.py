"""_st4 — SCHEMA ST4 detection, treedata parsing, leaf-only fetch (mocked fetch)."""

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.patterns import _st4

_BASE = "https://manual.yamaha.com/av/20/rxa8a/en-US"

# The entry page a user pastes: no ST4 token in the generator, only the
# stylesheet name. Identifiable by its tells alone.
_INDEX = """<!DOCTYPE html><html xmlns:st4="http://www.schema.de/2010/ST4/XmlImport">
<head><meta name="generator" content="Stylesheet-Folder:YMH_HTML_Manual_RC, Stylesheet-version:2024.3.1" />
<script type="text/javascript" charset="utf-8" src="js/treedata.json"></script></head>
<body><div class="container" role="main"><div class="tree1" id="tree1"></div></div></body></html>"""

# A topic page: announces ST4 outright.
_TOPIC_META = '<meta name="generator" content="SCHEMA ST4, Bootstrap 2016 v1" />'

# treedata.json is a JS source file, not JSON: BOM, `tocData = `, the array,
# then six more top-level assignments.
_TREEDATA = (
    "﻿tocData = ["
    '{"text":"BEFORE USING THE UNIT","id":"100","href":"100.html","reused":false,"nodes":['
    '{"text":"Read me first","id":"110","href":"110.html","reused":false,"nodes":['
    '{"text":"How to use this guide","id":"111","href":"111.html","reused":false},'
    '{"text":"Glossary","id":"112","href":"112.html","reused":false}]}]},'
    '{"text":"APPENDIX","id":"200","href":"200.html","reused":false,"nodes":['
    '{"text":"Trademarks","id":"210","href":"210.html","reused":false}]}];\n'
    'tocHeading = "Table of Contents";\n'
    'languagesData = [{"name":"日本語"}];\n'
    'projectTitle = "[GUI]RX-A8A";\n'
)


def _topic(title: str, body: str) -> str:
    return f"""<!DOCTYPE html><html><head>{_TOPIC_META}</head><body>
<div class="schema-navbar" id="navbar">CHROME</div>
<div class="container" role="main">
<ol class="breadcrumb hidden-xs"><li>RX-A8A</li><li>{title}</li></ol>
<h1 class="heading">{title}</h1>
{body}
</div>
<div class="container-footer">(c)2021 Yamaha Corporation</div>
<script src="js/x.js"></script></body></html>"""


_PAGES = {
    f"{_BASE}/js/treedata.json": _TREEDATA,
    f"{_BASE}/111.html": _topic("How to use this guide", '<p class="description">Read it.</p>'),
    f"{_BASE}/112.html": _topic("Glossary", '<p class="description">Words.</p>'),
    f"{_BASE}/210.html": _topic("Trademarks", '<p class="description">Legal.</p>'),
    # Branch stubs — content-free shells populated client-side from treedata.
    f"{_BASE}/100.html": _topic("BEFORE USING THE UNIT", '<div class="tree1" id="tree1"></div>'),
    f"{_BASE}/110.html": _topic("Read me first", '<div class="tree1" id="tree1"></div>'),
    f"{_BASE}/200.html": _topic("APPENDIX", '<div class="tree1" id="tree1"></div>'),
    f"{_BASE}/index.html": _INDEX,
}


@pytest.fixture
def fetched(monkeypatch):
    """Record every URL fetched so branch-stub skipping is observable."""
    seen: list[str] = []

    def fake_fetch_text(url, **kwargs):
        seen.append(url)
        if url not in _PAGES:
            raise AssertionError(f"unexpected fetch: {url}")
        return url, _PAGES[url]

    monkeypatch.setattr(http, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    return seen


def test_detects_entry_page_by_tells_not_generator():
    """The pasted entry URL advertises the stylesheet, never 'ST4' — so tell
    matching is the only way in. Probing the generator alone reads it as an
    unrecognized site (the _paligo two-faces problem)."""
    assert "st4" not in _INDEX.lower().split("generator")[1][:80]
    assert _st4.is_st4(_INDEX)


def test_detects_topic_page_by_generator_meta():
    assert _st4.is_st4(_topic("Glossary", "<p>x</p>"))


def test_rejects_unrelated_html():
    assert not _st4.is_st4('<html><head><meta name="generator" content="Hugo 0.1"></head></html>')


def test_treedata_parses_through_bom_and_trailing_assignments():
    """Named .json and served application/json, but it is a JS source file.
    json.loads on the raw body fails outright."""
    toc = _st4.parse_treedata(_TREEDATA)
    assert [n["text"] for n in toc] == ["BEFORE USING THE UNIT", "APPENDIX"]


def test_project_title_strips_bracket_prefix():
    assert _st4.project_title(_TREEDATA) == "RX-A8A"


def test_malformed_treedata_raises_invalid_input():
    with pytest.raises(InvalidInputError):
        _st4.parse_treedata("﻿somethingElse = [1,2,3];\n")


def test_only_leaf_pages_are_fetched(tmp_path, fetched):
    """The 99 branch nodes are empty client-rendered shells. Fetching them
    yields content-free fragments AND loses every chapter title."""
    acq = _st4.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)

    assert acq.pages == 3
    for leaf in ("111.html", "112.html", "210.html"):
        assert f"{_BASE}/{leaf}" in fetched
    for branch in ("100.html", "110.html", "200.html"):
        assert f"{_BASE}/{branch}" not in fetched


def test_branch_titles_are_synthesized_from_the_tree(tmp_path, fetched):
    """Branch pages carry no content, so the TOC tree is the only source of
    chapter headings. Without this the manual loses all its structure."""
    _st4.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)
    merged = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((tmp_path / "raw").glob("*.html"))
    )

    assert "<h1>BEFORE USING THE UNIT</h1>" in merged
    assert "<h2>Read me first</h2>" in merged
    assert "<h1>APPENDIX</h1>" in merged


def test_chrome_and_breadcrumb_are_dropped(tmp_path, fetched):
    _st4.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)
    merged = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((tmp_path / "raw").glob("*.html"))
    )

    assert "breadcrumb" not in merged
    assert "CHROME" not in merged
    assert "Yamaha Corporation" not in merged
    assert "js/x.js" not in merged
    assert "Read it." in merged


def test_topic_h1_is_demoted_below_synthesized_headings(tmp_path, fetched):
    """A topic's own <h1 class="heading"> would outrank its synthesized chapter
    heading, inverting the hierarchy the tree encodes."""
    _st4.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)
    merged = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((tmp_path / "raw").glob("*.html"))
    )

    assert "<h1>How to use this guide</h1>" not in merged
    assert "How to use this guide" in merged


def test_query_string_is_stripped_from_cross_links(tmp_path, monkeypatch):
    """`href="210.html?page=5459235851"` is a viewer route that expands a branch;
    left alone it produces a duplicate target for the same page."""
    pages = dict(_PAGES)
    pages[f"{_BASE}/111.html"] = _topic(
        "How to use this guide", '<a href="210.html?page=5459235851">See trademarks</a>'
    )

    def fake_fetch_text(url, **kwargs):
        return url, pages[url]

    monkeypatch.setattr(http, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    _st4.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)
    merged = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((tmp_path / "raw").glob("*.html"))
    )

    assert "?page=" not in merged
    assert f"{_BASE}/210.html" in merged


def test_image_refs_resolve_above_the_language_dir(tmp_path, monkeypatch):
    """Content images are `../Images/png/<id>__Web.png` — they live at the MODEL
    dir, one level above the language dir."""
    pages = dict(_PAGES)
    pages[f"{_BASE}/111.html"] = _topic(
        "How to use this guide",
        '<figure><img src="../Images/png/27021602658914443__Web.png"/></figure>',
    )

    def fake_fetch_text(url, **kwargs):
        return url, pages[url]

    monkeypatch.setattr(http, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    _st4.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)
    merged = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((tmp_path / "raw").glob("*.html"))
    )

    assert "https://manual.yamaha.com/av/20/rxa8a/Images/png/27021602658914443__Web.png" in merged


def test_missing_treedata_raises_invalid_input(tmp_path, monkeypatch):
    def fake_fetch_text(url, **kwargs):
        if url.endswith("treedata.json"):
            raise OSError("404")
        return url, _INDEX

    monkeypatch.setattr(http, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    with pytest.raises(InvalidInputError):
        _st4.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)


def test_slug_combines_host_label_with_project_title(tmp_path, fetched):
    """`slug_from_host` yields one label for every model a publisher ships — the
    manual names itself in the same file that lists its topics."""
    acq = _st4.acquire(f"{_BASE}/index.html", tmp_path, slug="yamaha", title=None)

    assert acq.slug == "yamaha-rx-a8a"


def test_publication_base_accepts_file_or_directory():
    assert _st4.publication_base(f"{_BASE}/index.html") == _BASE
    assert _st4.publication_base(f"{_BASE}/") == _BASE
    assert _st4.publication_base(_BASE) == _BASE


def test_synthesized_heading_escapes_the_toc_text(tmp_path, monkeypatch):
    """TOC text is a vendor string interpolated straight into markup. Every
    sibling pattern escapes its titles; unescaped, a `<` or `&` in a chapter
    name corrupts the merged document."""
    tree = (
        "﻿tocData = ["
        '{"text":"Bass & <Treble>","id":"1","href":"1.html","reused":false,"nodes":['
        '{"text":"Topic","id":"2","href":"2.html","reused":false}]}];\n'
        'tocHeading = "T";\n'
    )
    pages = {
        f"{_BASE}/js/treedata.json": tree,
        f"{_BASE}/2.html": _topic("Topic", "<p>x</p>"),
        f"{_BASE}/index.html": _INDEX,
    }
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, pages[url]))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    _st4.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)
    body = next((tmp_path / "raw").glob("*.html")).read_text(encoding="utf-8")

    assert "<h1>Bass &amp; &lt;Treble&gt;</h1>" in body


def test_publication_base_keeps_a_dotted_directory_segment():
    """A bare `"." in last` test strips any segment holding a dot — including a
    versioned directory — silently pointing treedata.json at the parent."""
    assert _st4.publication_base("https://h.test/manual/v2.1") == "https://h.test/manual/v2.1"
    assert (
        _st4.publication_base("https://h.test/manual/en-US/index.html")
        == "https://h.test/manual/en-US"
    )


def test_pages_lost_to_fetch_errors_are_counted(tmp_path, monkeypatch):
    """Throttling drops pages one at a time; without a count the deliverable
    reports truncated=False and audits clean while missing content."""

    def fake(url, **kwargs):
        if url.endswith("112.html"):
            raise OSError("503 throttled")
        return url, _PAGES[url]

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _st4.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)

    assert acq.pages == 2
    assert acq.lost == 1


def test_a_leaf_without_the_content_container_counts_as_lost(tmp_path, monkeypatch):
    """A leaf that fetches 200 but yields no `div.container[role="main"]` is lost, not staged."""
    no_container = (
        f"<!DOCTYPE html><html><head>{_TOPIC_META}</head>"
        "<body><div id='wrap'>restyled shell</div></body></html>"
    )

    def fake(url, **kwargs):
        if url.endswith("112.html"):
            return url, no_container
        return url, _PAGES[url]

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _st4.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)

    assert acq.lost == 1
    assert acq.pages == 2
    assert not any("112" in p.name for p in acq.raw_dir.glob("*.html"))


def test_an_href_holding_a_path_separator_is_flattened_into_the_filename(tmp_path, monkeypatch):
    """The href is remote-controlled (treedata.json); one holding "/" named a
    directory that was never created, so the whole acquire died with
    FileNotFoundError."""
    tree = 'tocData = [{"text":"Topic","id":"1","href":"topics/foo.html","reused":false}];\n'
    pages = {
        f"{_BASE}/js/treedata.json": tree,
        f"{_BASE}/topics/foo.html": _topic("Topic", '<p class="description">Read it.</p>'),
    }
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, pages[url]))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _st4.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)

    assert acq.pages == 1
    assert [p.name for p in acq.raw_dir.glob("*.html")] == ["0000-topics-foo.html"]
