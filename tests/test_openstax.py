"""openstax — match + Prev/Next-chain acquire + normalize (mocked fetch).

Reproduces REX's structure: content in <main class="page-content">, Prev/Next
as <a aria-label="…Page"> OUTSIDE main, content images as root-relative
/apps/image-cdn/… URLs, and page chrome (logo/toolbar) outside main.
"""

import urllib.error

from pagespring import http
from pagespring.patterns.openstax import OpenStaxPattern

_BASE = "https://openstax.org/books/anatomy-and-physiology-2e"
_PAGES = f"{_BASE}/pages"
_IMG = "/apps/image-cdn/v1/f=webp/apps/archive/20260407.195030/resources/deadbeef01"


def _page(title: str, prev: str | None, nxt: str | None, body: str) -> str:
    """One REX page: content inside <main>, nav anchors and chrome outside it."""
    prev_a = (
        f'<a aria-label="Previous Page" class="PrevNextBar__HidingContentLink-sc-x" '
        f'href="{prev}">Previous</a>'
        if prev
        else ""
    )
    next_a = (
        f'<a aria-label="Next Page" class="PrevNextBar__HidingContentLink-sc-y" '
        f'href="{nxt}">Next</a>'
        if nxt
        else ""
    )
    return f"""<!DOCTYPE html><html><head>
<title>{title} - Anatomy and Physiology 2e | OpenStax</title></head>
<body class="body"><div id="root">
  <div data-testid="navbar"><img src="/rex/releases/v4/abc/static/media/logo.svg"></div>
  <div data-testid="bookbanner"><h1 class="BookBanner__BookChapter">{title}</h1></div>
  <main class="page-content"><div id="main-content" class="main-content-styles">
    <div data-type="page" class="chapter-content-module">{body}</div>
  </div></main>
  <nav class="PrevNextBar__Wrapper">{prev_a}{next_a}</nav>
</div></body></html>"""


# A 3-page linked-list book: preface (first) -> 1-introduction -> 1-1 (last).
_TABLE = {
    f"{_PAGES}/preface": _page(
        "Preface",
        prev=None,
        nxt="1-introduction",
        body='<h2 data-type="document-title">Preface</h2><p>Welcome to A&amp;P.</p>',
    ),
    f"{_PAGES}/1-introduction": _page(
        "Introduction",
        prev="preface",
        nxt="1-1-overview-of-anatomy-and-physiology",
        body=(
            '<h2 data-type="document-title">Introduction</h2>'
            "<p>Though you may approach a course in anatomy and physiology.</p>"
            f'<img src="{_IMG}" alt="figure">'
        ),
    ),
    f"{_PAGES}/1-1-overview-of-anatomy-and-physiology": _page(
        "1.1 Overview",
        prev="1-introduction",
        nxt=None,
        body='<h2 data-type="document-title">1.1 Overview</h2><p>Anatomy is the study of structure.</p>',
    ),
}


def _fake_fetch_text(url, **kwargs):
    return url, _TABLE[url]


def test_match():
    p = OpenStaxPattern()
    assert p.match("https://openstax.org/books/anatomy-and-physiology-2e")
    assert p.match("https://openstax.org/books/microbiology/pages/preface")
    assert not p.match("https://openstax.org/subjects/science")
    assert not p.match("https://openstax.org/details/books/microbiology")
    assert not p.match("https://docs.example.com")


def test_acquire_walks_chain_from_bare_book_url(tmp_path, monkeypatch):
    """A bare /books/<slug> URL seeds at preface and walks Next to the end —
    extracting every page's content, absolutizing the content image, and leaving
    page chrome (logo, nav) out."""
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = OpenStaxPattern()

    acq = p.acquire(_BASE, tmp_path)
    assert acq.kind == "html"
    assert acq.slug == "anatomy-and-physiology-2e"
    assert acq.pages == 3

    html = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    # every page's body present, in reading order
    assert html.index("Preface") < html.index("Introduction") < html.index("1.1 Overview")
    assert "Anatomy is the study of structure." in html
    # content image absolutized to openstax.org; nothing left root-relative
    assert f"https://openstax.org{_IMG}" in html
    # page chrome excluded (lives outside <main>)
    assert "/rex/" not in html and "logo.svg" not in html
    assert "Previous Page" not in html and "Next Page" not in html
    assert "BookBanner__BookChapter" not in html


def test_seed_midbook_walks_back_to_first(tmp_path, monkeypatch):
    """Pointed at the LAST page, acquire walks Prev back to the first page, then
    Next forward — so the whole book is captured regardless of entry point."""
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = OpenStaxPattern()

    acq = p.acquire(f"{_PAGES}/1-1-overview-of-anatomy-and-physiology", tmp_path)
    assert acq.pages == 3  # walked back to preface, then forward through all three

    html = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert html.index("Preface") < html.index("1.1 Overview")


class _LogSpy:
    def __init__(self):
        self.warnings = []

    def warning(self, event, **kw):
        self.warnings.append((event, kw))

    def info(self, *a, **kw):
        pass


def test_page_cap_warns(tmp_path, monkeypatch):
    """Truncating a crawl at _MAX is loud, not silent."""
    from pagespring.patterns import openstax as mod

    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_MAX", 1)
    spy = _LogSpy()
    monkeypatch.setattr(mod, "log", spy)

    acq = OpenStaxPattern().acquire(_BASE, tmp_path)

    assert acq.pages == 1  # stopped at the cap
    assert any(event == "openstax.capped" for event, _ in spy.warnings)


def test_seed_404_is_clean(tmp_path, monkeypatch):
    """A bare book URL whose preface 404s raises (no traceback swallowed)."""

    def fetch_404(url, **kwargs):
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    monkeypatch.setattr(http, "fetch_text", fetch_404)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    try:
        OpenStaxPattern().acquire(_BASE, tmp_path)
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    else:
        raise AssertionError("expected the seed 404 to propagate")


def test_book_title_comes_from_page_title_not_slug(tmp_path, monkeypatch):
    """The deliverable title is read from the page <title> ('… - <Book> |
    OpenStax'), so slugs that drop words (concepts-biology -> 'Concepts of
    Biology') don't mangle it."""
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = OpenStaxPattern()

    acq = p.acquire(_BASE, tmp_path)
    assert acq.title == "Anatomy and Physiology 2e"

    html = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "<h1>Anatomy and Physiology 2e</h1>" in html
    assert "<title>Anatomy and Physiology 2e</title>" in html
