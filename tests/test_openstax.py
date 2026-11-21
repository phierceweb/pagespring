"""openstax over REX's shape: content in ``<main class="page-content">``, Prev/Next and chrome
outside it, root-relative images (mocked fetch)."""

import json
import urllib.error

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns._openstax_toc import TREE_NAME
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
    """Slugs drop words (concepts-biology), so the title comes from the page ``<title>``."""
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = OpenStaxPattern()

    acq = p.acquire(_BASE, tmp_path)
    assert acq.title == "Anatomy and Physiology 2e"

    html = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "<h1>Anatomy and Physiology 2e</h1>" in html
    assert "<title>Anatomy and Physiology 2e</title>" in html


def _shell(prev: str, nxt: str) -> str:
    """A REX page that serves 200 and navigates on, but has no content container."""
    return (
        "<!DOCTYPE html><html><head><title>Shell | OpenStax</title></head>"
        '<body><div id="root"><nav class="PrevNextBar__Wrapper">'
        f'<a aria-label="Previous Page" href="{prev}">Previous</a>'
        f'<a aria-label="Next Page" href="{nxt}">Next</a>'
        "</nav></div></body></html>"
    )


def test_page_without_content_container_counts_as_lost(tmp_path, monkeypatch):
    """A 200 that renders no <main class="page-content"> is a page discovered and
    never staged; uncounted it leaves a hole audit reads as a complete crawl."""
    table = dict(_TABLE)
    table[f"{_PAGES}/1-introduction"] = _shell(
        prev="preface", nxt="1-1-overview-of-anatomy-and-physiology"
    )

    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, table[url]))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = OpenStaxPattern().acquire(_BASE, tmp_path)

    assert acq.pages == 2
    assert acq.lost == 1
    assert acq.truncated is False


def test_midchain_fetch_failure_truncates_rather_than_losing_one_page(tmp_path, monkeypatch):
    """The Next chain is the only way forward, so a dead link strands every page
    after it — a partial deliverable, not a single missing page."""

    def fake(url, **kwargs):
        if url.endswith("/1-introduction"):
            raise OSError("503 throttled")
        return url, _TABLE[url]

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = OpenStaxPattern().acquire(_BASE, tmp_path)

    assert acq.pages == 1
    assert acq.truncated is True
    assert acq.lost == 0


_CH1_TITLE = (
    '<span class="os-number">1</span><span class="os-divider"> </span>'
    '<span class="os-text">An Introduction to the Human Body</span>'
)
_TREE = {
    "id": "book",
    "title": "Anatomy and Physiology 2e",
    "contents": [
        {
            "title": "Preface",
            "toc_type": "book-content",
            "toc_target_type": "preface",
            "slug": "preface",
        },
        {
            "title": "Levels of Organization",
            "toc_type": "unit",
            "contents": [
                {
                    "title": _CH1_TITLE,
                    "toc_type": "chapter",
                    "contents": [
                        {
                            "title": "Introduction",
                            "toc_type": "book-content",
                            "toc_target_type": "intro",
                            "slug": "1-introduction",
                        },
                        {
                            "title": "1.1 Overview",
                            "toc_type": "book-content",
                            "toc_target_type": "numbered-section",
                            "slug": "1-1-overview-of-anatomy-and-physiology",
                        },
                    ],
                }
            ],
        },
    ],
}


def _with_state(page: str, tree: dict) -> str:
    """REX's embedded state, after ``</main>`` as the reader site serves it."""
    state = json.dumps({"content": {"book": {"tree": tree}}}).replace("<", "\\u003c")
    return page.replace("</body>", f"<script>window.__PRELOADED_STATE__ = {state};</script></body>")


def test_acquire_keeps_the_book_tree_from_the_pages(tmp_path, monkeypatch):
    table = {url: _with_state(page, _TREE) for url, page in _TABLE.items()}
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, table[url]))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = OpenStaxPattern()

    acq = p.acquire(_BASE, tmp_path)
    assert json.loads((acq.raw_dir / TREE_NAME).read_text(encoding="utf-8")) == _TREE
    assert sorted(f.name for f in acq.raw_dir.glob("*.html")) == [
        "0000.html",
        "0001.html",
        "0002.html",
    ]

    html = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "__PRELOADED_STATE__" not in html
    chapter = (
        "<h1>1 An Introduction to the Human Body</h1>\n<!-- source: " + f"{_PAGES}/1-introduction"
    )
    assert chapter in html
    assert html.index("<h1>Anatomy and Physiology 2e</h1>") < html.index("Welcome to A&amp;P.")
    assert html.index("Welcome to A&amp;P.") < html.index(chapter)


def test_acquire_takes_the_tree_from_a_later_page_when_the_first_lacks_it(tmp_path, monkeypatch):
    from pagespring.patterns import openstax as mod

    first = f"{_PAGES}/preface"
    table = {
        url: page if url == first else _with_state(page, _TREE) for url, page in _TABLE.items()
    }
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, table[url]))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    spy = _LogSpy()
    monkeypatch.setattr(mod, "log", spy)

    acq = OpenStaxPattern().acquire(_BASE, tmp_path)

    assert json.loads((acq.raw_dir / TREE_NAME).read_text(encoding="utf-8")) == _TREE
    assert spy.warnings == []


def test_acquire_without_a_book_tree_warns_and_saves_every_page(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    from pagespring.patterns import openstax as mod

    spy = _LogSpy()
    monkeypatch.setattr(mod, "log", spy)

    acq = OpenStaxPattern().acquire(_BASE, tmp_path)

    assert acq.pages == 3
    assert not (acq.raw_dir / TREE_NAME).exists()
    assert [event for event, _ in spy.warnings] == ["openstax.book_tree_missing"]


_BOOK = "https://openstax.org/books/calculus-volume-1/pages"
_RAW_SLUGS = [
    "preface",
    "1-introduction",
    "1-1-review-of-functions",
    "2-introduction",
    "a-table-of-integrals",
    "chapter-1",
    "chapter-2",
    "index",
]


def _span(number: str, divider: str, text: str) -> str:
    return (
        f'<span class="os-number">{number}</span><span class="os-divider">{divider}</span>'
        f'<span class="os-text">{text}</span>'
    )


def _leaf(slug: str, target: str, title: str = "x") -> dict:
    return {"title": title, "toc_type": "book-content", "toc_target_type": target, "slug": slug}


_BACK_TREE = {
    "contents": [
        _leaf("preface", "preface", "Preface"),
        {
            "title": _span("1", " ", "Functions &amp; Graphs"),
            "toc_type": "chapter",
            "contents": [_leaf("1-introduction", "intro"), _leaf("1-1-review-of-functions", "x")],
        },
        {
            "title": _span("2", " ", "Limits"),
            "toc_type": "chapter",
            "contents": [_leaf("2-introduction", "intro")],
        },
        _leaf("a-table-of-integrals", "appendix", _span("A", " | ", "Table of Integrals")),
        {
            "title": "Answer Key",
            "toc_type": "sub-book-tree",
            "slug": "answer-key",
            "contents": [_leaf("chapter-1", "answer-key"), _leaf("chapter-2", "answer-key")],
        },
        _leaf("index", "index", "Index"),
    ]
}


def _raw(tmp_path, tree: dict | None) -> AcquireResult:
    raw = tmp_path / "raw"
    raw.mkdir(parents=True)
    for i, slug in enumerate(_RAW_SLUGS):
        (raw / f"{i:04d}.html").write_text(
            f"<!-- source: {_BOOK}/{slug} -->\n<section>\n<p>{slug} body</p>\n</section>\n",
            encoding="utf-8",
        )
    if tree is not None:
        (raw / TREE_NAME).write_text(json.dumps(tree), encoding="utf-8")
    return AcquireResult(
        raw_dir=raw,
        kind="html",
        slug="calculus-volume-1",
        pages=len(_RAW_SLUGS),
        title="Calculus Volume 1",
    )


def _normalized(tmp_path, tree: dict | None) -> str:
    out = OpenStaxPattern().normalize(_raw(tmp_path, tree), tmp_path)
    return out.read_text(encoding="utf-8")


def test_normalize_heads_each_chapter_and_back_matter_node(tmp_path):
    html = _normalized(tmp_path, _BACK_TREE)
    h1s = [line for line in html.splitlines() if line.startswith("<h1>")]
    assert h1s == [
        "<h1>Calculus Volume 1</h1>",
        "<h1>1 Functions &amp; Graphs</h1>",
        "<h1>2 Limits</h1>",
        "<h1>A | Table of Integrals</h1>",
        "<h1>Answer Key</h1>",
        "<h1>Index</h1>",
    ]
    assert html.index("<h1>Calculus Volume 1</h1>") < html.index("preface body")
    assert html.index("preface body") < html.index("<h1>1 Functions &amp; Graphs</h1>")
    for heading, slug in [
        ("1 Functions &amp; Graphs", "1-introduction"),
        ("2 Limits", "2-introduction"),
        ("A | Table of Integrals", "a-table-of-integrals"),
        ("Answer Key", "chapter-1"),
        ("Index", "index"),
    ]:
        assert f"<h1>{heading}</h1>\n<!-- source: {_BOOK}/{slug} -->" in html
    assert "<!-- source: " + f"{_BOOK}/chapter-2 -->" in html


def test_removing_the_inserted_headings_gives_the_no_tree_output(tmp_path):
    with_tree = _normalized(tmp_path / "a", _BACK_TREE)
    without = _normalized(tmp_path / "b", None)
    lines = with_tree.splitlines(keepends=True)
    first_h1 = next(i for i, line in enumerate(lines) if line.startswith("<h1>"))
    kept = [line for i, line in enumerate(lines) if i == first_h1 or not line.startswith("<h1>")]
    assert "".join(kept) == without
    assert len(lines) - len(kept) == 5


def test_mismatched_tree_normalizes_as_without_one(tmp_path, monkeypatch):
    from pagespring.patterns import _openstax_toc

    spy = _LogSpy()
    monkeypatch.setattr(_openstax_toc, "log", spy)
    tree = json.loads(json.dumps(_BACK_TREE))
    tree["contents"][2]["contents"] = []  # 2-introduction no longer in the tree

    assert _normalized(tmp_path / "a", tree) == _normalized(tmp_path / "b", None)
    assert [(event, kw["reason"]) for event, kw in spy.warnings] == [
        ("openstax.tree_mismatch", "page_not_in_tree")
    ]


def test_a_page_whose_tree_nests_deeper_than_any_book_is_crawled_without_one(tmp_path, monkeypatch):
    from pagespring.patterns import openstax as mod

    leaf = '{"title": "x", "toc_type": "book-content", "slug": "preface"}'
    tree = '{"contents": [' + '{"title": "x", "toc_type": "chapter", "contents": [' * 600
    tree += leaf + "]}" * 600 + "]}"
    state = f'<script>window.__PRELOADED_STATE__ = {{"content": {{"book": {{"tree": {tree}}}}}}}</script>'
    table = {url: page.replace("</body>", state + "</body>") for url, page in _TABLE.items()}
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, table[url]))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    spy = _LogSpy()
    monkeypatch.setattr(mod, "log", spy)

    acq = OpenStaxPattern().acquire(_BASE, tmp_path)

    assert acq.pages == 3
    assert not (acq.raw_dir / TREE_NAME).exists()
    assert [event for event, _ in spy.warnings] == ["openstax.book_tree_missing"]


def test_the_deepest_tree_book_tree_accepts_is_written(tmp_path, monkeypatch):
    """Whatever ``book_tree`` accepts, acquire can write: the JSON writer recurses per container."""
    from pagespring.patterns import _openstax_toc

    depth = 2 * _openstax_toc._MAX_NESTING
    leaf = '{"title": "x", "toc_type": "book-content", "slug": "preface"}'
    tree = f'{{"contents": [{leaf}], "extra": {"[" * depth + "]" * depth}}}'
    state = f'<script>window.__PRELOADED_STATE__ = {{"content": {{"book": {{"tree": {tree}}}}}}}</script>'
    assert _openstax_toc.book_tree(state) is not None
    table = {url: page.replace("</body>", state + "</body>") for url, page in _TABLE.items()}
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, table[url]))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    def acquire_below(frames: int):
        # Headroom: the deepest accepted tree must write well inside the recursion limit.
        return acquire_below(frames - 1) if frames else OpenStaxPattern().acquire(_BASE, tmp_path)

    acq = acquire_below(300)

    assert (acq.raw_dir / TREE_NAME).is_file()


def test_a_tree_nested_past_the_cap_off_contents_is_crawled_without_one(tmp_path, monkeypatch):
    from pagespring.patterns import openstax as mod

    deep = "[" * 1000 + "]" * 1000
    leaf = '{"title": "x", "toc_type": "book-content", "slug": "preface"}'
    tree = f'{{"contents": [{leaf}], "extra": {deep}}}'
    state = f'<script>window.__PRELOADED_STATE__ = {{"content": {{"book": {{"tree": {tree}}}}}}}</script>'
    table = {url: page.replace("</body>", state + "</body>") for url, page in _TABLE.items()}
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, table[url]))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    spy = _LogSpy()
    monkeypatch.setattr(mod, "log", spy)

    acq = OpenStaxPattern().acquire(_BASE, tmp_path)

    assert acq.pages == 3
    assert not (acq.raw_dir / TREE_NAME).exists()
    assert [event for event, _ in spy.warnings] == ["openstax.book_tree_missing"]


def test_a_tree_holding_a_lone_surrogate_is_crawled_without_one(tmp_path, monkeypatch):
    from pagespring.patterns import openstax as mod

    tree = (
        '{"contents": [{"title": "Pre\\ud800face", "toc_type": "book-content", "slug": "preface"}]}'
    )
    state = f'<script>window.__PRELOADED_STATE__ = {{"content": {{"book": {{"tree": {tree}}}}}}}</script>'
    table = {url: page.replace("</body>", state + "</body>") for url, page in _TABLE.items()}
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, table[url]))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    spy = _LogSpy()
    monkeypatch.setattr(mod, "log", spy)

    acq = OpenStaxPattern().acquire(_BASE, tmp_path)

    assert acq.pages == 3
    assert not (acq.raw_dir / TREE_NAME).exists()
    assert [event for event, _ in spy.warnings] == ["openstax.book_tree_missing"]
