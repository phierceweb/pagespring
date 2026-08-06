"""_clickhelp — ClickHelp webHelp exports (mocked fetch).

ClickHelp emits NO ``<meta name="generator">``, so docs_probe's meta sniff can
never claim it — the tells are its own asset paths (``CHWebHelp.css``,
``_webHelpScripts/Master/``) and ``<body class="WebHelp_body">``.

The whole page index is one JS file, ``_webHelpScripts/Master/toc_nav.js``,
holding the TOC as data plus the URL template. No crawling required.
"""

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.patterns import _clickhelp

ROOT = "https://vendor.example/manuals/Widget/Manual"
ENTRY = f"{ROOT}/HTML/welcome.html"

_TOC = """
window['tocTree']=new CHTree('pnlToc',[
 {"e":"welcome","t":"Welcome"},
 {"e":"intro","t":"Intro","f":"1"},
 {"e":"setup","t":"Setup"}
]);
window['webHelpTopicUrlTemplate']='{{externalId}}.html';
"""

_PAGE = """<html><head><link href="../_webHelpStyles/CHWebHelp.css"></head>
<body class="WebHelp_body">
<div class="CHMenu">nav chrome</div>
<article class="TopicViewer_container">
  <div id="pnlTopicContentContainer" class="TopicViewer_contentContainer">
    <h1 id="pnlTitle" class="ArticleEditor_title">Setup</h1>
    <p>Insert the plug-in on a track.</p>
    <img src="../Storage/pub-v1/setup/1%20main%20ui.png">
    <div class="footer">© Vendor</div>
    <a class="CHNavLinkNext" href="next.html">Next</a>
    <a class="CHNavLinkPrevious" href="prev.html">Previous</a>
    <div class="CHMiniToc"><a href="#s1">On this page</a></div>
  </div>
</article>
</body></html>"""


def _fetch(seen=None, *, toc=_TOC, toc_ok=True):
    def fetch(url, **kwargs):
        if seen is not None:
            seen.append(url)
        if url.endswith("toc_nav.js"):
            if not toc_ok:
                raise OSError("404")
            return url, toc
        return url, _PAGE

    return fetch


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def test_detects_clickhelp_without_a_generator_meta():
    assert _clickhelp.is_clickhelp(_PAGE) is True
    assert _clickhelp.is_clickhelp("<html><body>plain</body></html>") is False


def test_manual_root_is_derived_from_a_topic_url():
    """Topics live at <root>/HTML/<id>.html; the TOC lives at <root>/_webHelpScripts/."""
    assert _clickhelp.manual_root(ENTRY) == ROOT
    assert _clickhelp.manual_root(f"{ROOT}/HTML/deep-topic.html") == ROOT


def test_fetches_the_toc_then_every_topic(tmp_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(http, "fetch_text", _fetch(seen))

    acq = _clickhelp.acquire(ENTRY, tmp_path, slug="widget", title=None)

    assert seen[0] == f"{ROOT}/_webHelpScripts/Master/toc_nav.js"
    assert acq.pages == 3  # welcome + intro + setup, folder node included (it has a page)
    for pid in ("welcome", "intro", "setup"):
        assert f"{ROOT}/HTML/{pid}.html" in seen


def test_keeps_the_content_container_and_strips_in_container_chrome(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fetch())

    acq = _clickhelp.acquire(ENTRY, tmp_path, slug="widget", title=None)
    joined = "\n".join(p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html")))

    assert "Insert the plug-in on a track." in joined
    assert "nav chrome" not in joined  # outside the container
    # These two sit INSIDE the container on every real page, so an outer-only
    # strip leaves a copyright line and a "Next" link on each topic.
    assert "© Vendor" not in joined
    # All three pager/mini-TOC variants, not just Next: an independent audit of the
    # real VocAlign ingest found 27 surviving Previous links and 117 CHMiniToc
    # entries, because only the one selector I happened to see was stripped.
    assert "CHNavLinkNext" not in joined
    assert "CHNavLinkPrevious" not in joined
    assert "CHMiniToc" not in joined


def test_absolutizes_relative_asset_refs(tmp_path, monkeypatch):
    """Images are ../Storage/... relative to the topic, with %20-escaped names."""
    monkeypatch.setattr(http, "fetch_text", _fetch())

    acq = _clickhelp.acquire(ENTRY, tmp_path, slug="widget", title=None)
    joined = "\n".join(p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html")))

    assert f"{ROOT}/Storage/pub-v1/setup/1%20main%20ui.png" in joined
    assert "../Storage" not in joined


def test_slug_comes_from_the_url_path_not_the_host(tmp_path, monkeypatch):
    """One vendor hosts many manuals under one host — a host-derived slug would
    collide across products (synchroarts ships VocAlign, Revoice Pro, RePitch...)."""
    assert _clickhelp.slug_from_path(ENTRY) == "widget"
    assert _clickhelp.slug_from_path(
        "https://x.example/manuals/VocAlign6Pro/Manual/HTML/a.html"
    ) == ("vocalign6pro")


def test_missing_toc_is_an_input_error(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fetch(toc_ok=False))
    with pytest.raises(InvalidInputError, match="toc_nav.js"):
        _clickhelp.acquire(ENTRY, tmp_path, slug="widget", title=None)


def test_toc_without_topics_is_an_input_error(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fetch(toc="window['x']=1;"))
    with pytest.raises(InvalidInputError, match="no topics"):
        _clickhelp.acquire(ENTRY, tmp_path, slug="widget", title=None)


def test_a_topic_without_the_content_container_counts_as_lost(tmp_path, monkeypatch):
    """A 200 topic whose container is absent was dropped silently — only fetch
    errors counted, so a theme change audited clean while shipping short."""
    no_container = "<html><body class='WebHelp_body'><div>theme changed</div></body></html>"

    def fetch(url, **kwargs):
        if url.endswith("toc_nav.js"):
            return url, _TOC
        if url.endswith("/setup.html"):
            return url, no_container
        return url, _PAGE

    monkeypatch.setattr(http, "fetch_text", fetch)

    acq = _clickhelp.acquire(ENTRY, tmp_path, slug="widget", title=None)

    assert acq.pages == 2
    assert acq.lost == 1
    assert not any("setup" in p.name for p in acq.raw_dir.glob("*.html"))


def test_a_topic_id_holding_a_path_separator_is_flattened_into_the_filename(tmp_path, monkeypatch):
    """The tid is remote-controlled; one holding "/" named a directory that was
    never created, so the whole acquire died with FileNotFoundError."""
    seen: list[str] = []
    toc = """window['tocTree']=new CHTree('pnlToc',[{"e":"guide/setup","t":"Setup"}]);"""
    monkeypatch.setattr(http, "fetch_text", _fetch(seen, toc=toc))

    acq = _clickhelp.acquire(ENTRY, tmp_path, slug="widget", title=None)

    assert [p.name for p in acq.raw_dir.glob("*.html")] == ["0000-guide-setup.html"]
    assert f"{ROOT}/HTML/guide/setup.html" in seen  # the URL keeps the id verbatim
