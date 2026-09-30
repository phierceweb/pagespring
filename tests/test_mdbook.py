"""_mdbook — the generator-comment tell, the print page, and the TOC-ordered crawl."""

import re
from urllib.error import HTTPError

import pytest
from bs4 import BeautifulSoup
from pf_core.exceptions import InvalidInputError
from structlog.testing import capture_logs

from pagespring import http
from pagespring.patterns import _mdbook
from pagespring.patterns.docs_probe import DocsProbePattern

_ROOT = "https://books.test/guide/"
_SEP = '<div style="break-before: page; page-break-before: always;"></div>'


def _shell(
    main: str, *, ptr: str = "", title: str = "Intro - Widget Guide", sidebar: str = ""
) -> str:
    """An mdBook page: generator comment, ``path_to_root``, chrome around ``<main>``."""
    menu = '<h1 class="menu-title">Widget Guide</h1>' if "Widget" in title else ""
    return f"""<!DOCTYPE HTML>
<html lang="en" class="light sidebar-visible" dir="ltr">
<head>
<!-- Book generated using mdBook -->
<meta charset="UTF-8"><title>{title}</title>
<script>
    const path_to_root = "{ptr}";
</script>
<script src="{ptr}toc-1a2b.js"></script>
</head>
<body>
<nav id="mdbook-sidebar" class="sidebar" aria-label="Table of contents">
{sidebar or '<mdbook-sidebar-scrollbox class="sidebar-scrollbox"></mdbook-sidebar-scrollbox>'}
<noscript><iframe class="sidebar-iframe-outer" src="{ptr}toc.html"></iframe></noscript>
</nav>
<div id="mdbook-page-wrapper" class="page-wrapper"><div class="page">
<div id="mdbook-menu-bar" class="menu-bar sticky">{menu}</div>
<div id="mdbook-content" class="content"><main>
{main}
</main>
<nav class="nav-wrapper" aria-label="Page navigation"><a rel="next" href="x.html">PAGER</a></nav>
</div></div></div>
<script src="{ptr}book-9f.js"></script>
</body></html>"""


# The real TOC leaves a parent <li> unclosed around its <ol class="section">.
_TOC = """<!DOCTYPE HTML><html><head>
<!-- sidebar iframe generated using mdBook -->
</head><body class="sidebar-iframe-inner"><ol class="chapter">
<li class="chapter-item expanded "><span class="chapter-link-wrapper"><a href="index.html" target="_parent">Introduction</a></span></li>
<li class="chapter-item expanded "><li class="part-title">User guide</li></li>
<li class="chapter-item expanded "><span class="chapter-link-wrapper"><a href="cli/index.html" target="_parent"><strong aria-hidden="true">1.</strong> Command-line tool</a></span><ol class="section">
<li class="chapter-item expanded "><span class="chapter-link-wrapper"><a href="cli/build.html" target="_parent"><strong aria-hidden="true">1.1.</strong> build</a></span></li>
<li class="chapter-item expanded "><span class="chapter-link-wrapper"><span><strong aria-hidden="true">1.2.</strong> Draft chapter</span></span></li>
</ol>
<li class="chapter-item expanded "><span class="chapter-link-wrapper"><a href="faq.html" target="_parent"><strong aria-hidden="true">2.</strong> FAQ</a></span></li>
</ol></body></html>"""

_PRINT_MAIN = f"""<h1 id="introduction"><a class="header" href="#introduction">Introduction</a></h1>
<p>Welcome. See <a href="#the-build-command">build</a>.</p>
<img src="img/logo.png" srcset="img/logo.png 1x, img/logo-2x.png 2x"/>
{_SEP}
<h1 id="command-line-tool"><a class="header" href="#command-line-tool">Command-line tool</a></h1>
<p>CLI overview.</p>
{_SEP}
<h1 id="the-build-command"><a class="header" href="#the-build-command">The build command</a></h1>
<p>Builds it. See <a href="../std/index.html">std</a>.</p>
<h2 id="options">Options</h2>
<h4 id="--open">--open</h4>
<footer>FOOTCHROME</footer>
{_SEP}
<h1 id="faq">FAQ</h1>
<p>Answers.</p>
<script>window.print()</script>"""

_BOOK = {
    _ROOT: _shell("<h1>Introduction</h1><p>Welcome.</p>"),
    f"{_ROOT}toc.html": _TOC,
    f"{_ROOT}print.html": _shell(_PRINT_MAIN, title="Widget Guide"),
    f"{_ROOT}cli/build.html": _shell(
        "<h1>The build command</h1><p>Builds it.</p>", ptr="../", title="build - Widget Guide"
    ),
}


def _serve(monkeypatch, pages: dict[str, str], *, missing: tuple[str, ...] = ()) -> list[str]:
    """Mock ``http.fetch_text`` over ``pages``; a directory URL without its slash
    redirects to the slashed form, and ``missing`` URLs 404."""
    seen: list[str] = []

    def fake(url, **kwargs):
        seen.append(url)
        if url in missing:
            raise HTTPError(url, 404, "Not Found", {}, None)
        if url not in pages and f"{url}/" in pages:
            url = f"{url}/"
        if url not in pages:
            raise AssertionError(f"unexpected fetch: {url}")
        return url, pages[url]

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    return seen


def _staged(acq) -> list[str]:
    return [p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html"))]


def _headings(fragment: str) -> list[tuple[int, str]]:
    return [
        (int(level), re.sub(r"<[^>]+>", "", text).strip())
        for level, text in re.findall(r"<h([1-6])[^>]*>(.*?)</h\1>", fragment, re.S)
    ]


# --- the tell ---------------------------------------------------------------


def test_the_generator_comment_is_the_tell():
    assert _mdbook.is_mdbook(_BOOK[_ROOT])


def test_an_older_theme_with_the_comment_is_mdbook():
    page = '<html><head>\n        <!-- Book generated using mdBook -->\n<meta charset="UTF-8">'
    assert _mdbook.is_mdbook(page)


@pytest.mark.parametrize(
    "page",
    [
        # A page about mdBook quotes the comment as escaped text.
        "<html><body><pre><code>&lt;!-- Book generated using mdBook --&gt;</code></pre></body></html>",
        # The TOC iframe carries a different comment.
        _TOC,
        '<html><head><meta name="generator" content="Docusaurus v3.8.1"></head></html>',
        '<html><head><meta name="generator" content="Hugo 0.140"></head><body>mdBook</body></html>',
        '<html><head><script src="_static/documentation_options.js"></script></head></html>',
    ],
)
def test_other_pages_are_not_mdbook(page):
    assert not _mdbook.is_mdbook(page)


# --- the print page ---------------------------------------------------------


def test_the_print_page_stages_every_chapter_without_a_crawl(tmp_path, monkeypatch):
    seen = _serve(monkeypatch, _BOOK)

    acq = _mdbook.acquire(_ROOT, tmp_path, slug="books", title=None)

    assert acq.pages == 4
    assert acq.kind == "html"
    assert acq.lost == 0 and acq.truncated is False
    assert seen == [_ROOT, f"{_ROOT}toc.html", f"{_ROOT}print.html"]


def test_a_chapter_at_a_long_path_stages_under_a_short_file_name(tmp_path, monkeypatch):
    long_name = "a" * 300  # past a file name's 255-byte limit
    _serve(monkeypatch, {**_BOOK, f"{_ROOT}toc.html": _TOC.replace("faq", long_name)})

    acq = _mdbook.acquire(_ROOT, tmp_path, slug="books", title=None)

    assert acq.pages == 4
    assert max(len(p.name) for p in acq.raw_dir.iterdir()) <= 100
    heads = [_headings(f)[0][1] for f in _staged(acq)]
    assert heads == ["Introduction", "Command-line tool", "The build command", "FAQ"]


def test_nested_chapters_sit_under_their_parents(tmp_path, monkeypatch):
    """mdBook renders every chapter's title as <h1>; the TOC depth restores nesting."""
    _serve(monkeypatch, _BOOK)

    acq = _mdbook.acquire(_ROOT, tmp_path, slug="books", title=None)

    build = _staged(acq)[2]
    assert _headings(build) == [(2, "The build command"), (3, "Options"), (5, "--open")]
    assert _headings(_staged(acq)[3]) == [(1, "FAQ")]


def test_print_chrome_is_stripped_and_refs_absolutized(tmp_path, monkeypatch):
    _serve(monkeypatch, _BOOK)

    acq = _mdbook.acquire(_ROOT, tmp_path, slug="books", title=None)
    merged = "\n".join(_staged(acq))

    assert "PAGER" not in merged
    assert "FOOTCHROME" not in merged
    assert "window.print" not in merged
    assert "page-break-before" not in merged
    assert 'src="https://books.test/guide/img/logo-2x.png"' in merged
    assert "srcset" not in merged
    assert 'href="https://books.test/std/index.html"' in merged
    assert 'href="#the-build-command"' in merged


def test_each_chapter_names_its_own_page_as_source(tmp_path, monkeypatch):
    _serve(monkeypatch, _BOOK)

    acq = _mdbook.acquire(_ROOT, tmp_path, slug="books", title=None)

    assert _staged(acq)[2].startswith(f"<!-- source: {_ROOT}cli/build.html -->")


def test_a_chapter_seed_scopes_to_its_own_book(tmp_path, monkeypatch):
    seen = _serve(monkeypatch, _BOOK)

    acq = _mdbook.acquire(f"{_ROOT}cli/build.html", tmp_path, slug="books", title=None)

    assert acq.pages == 4
    assert seen == [f"{_ROOT}cli/build.html", f"{_ROOT}toc.html", f"{_ROOT}print.html"]


def test_a_directory_seed_gets_its_slash_back(tmp_path, monkeypatch):
    """docs_probe hands over the URL with its trailing slash stripped; a server that
    served the page there would resolve every relative ref one level too high."""
    pages = {**_BOOK, _ROOT.rstrip("/"): _BOOK[_ROOT]}
    seen = _serve(monkeypatch, pages)

    _mdbook.acquire(_ROOT.rstrip("/"), tmp_path, slug="books", title=None)

    assert seen[0] == _ROOT
    assert f"{_ROOT}print.html" in seen


def test_the_book_title_names_the_slug_and_the_deliverable(tmp_path, monkeypatch):
    """One host serves many books; the host label alone collides."""
    _serve(monkeypatch, _BOOK)

    acq = _mdbook.acquire(_ROOT, tmp_path, slug="books", title="Intro - Widget Guide")

    assert acq.title == "Widget Guide"
    assert acq.slug == "books-widget-guide"


def test_without_a_menu_title_the_print_title_names_the_book(tmp_path, monkeypatch):
    pages = {
        **_BOOK,
        _ROOT: _shell("<h1>Intro</h1>", title="Intro | Getting started"),
        f"{_ROOT}print.html": _shell(_PRINT_MAIN, title="Gadget Manual"),
    }
    _serve(monkeypatch, pages)

    acq = _mdbook.acquire(_ROOT, tmp_path, slug="books", title="Intro | Getting started")

    assert acq.title == "Gadget Manual"
    assert acq.slug == "books-gadget-manual"


def test_a_print_page_that_disagrees_with_the_toc_is_not_trusted(tmp_path, monkeypatch):
    """Depth is assigned by position; a chapter count that differs from the TOC's
    would nest the wrong chapters, so the pages are crawled instead."""
    short_print = _PRINT_MAIN.split(_SEP)[0]
    pages = {
        **_BOOK,
        f"{_ROOT}print.html": _shell(short_print, title="Widget Guide"),
        f"{_ROOT}index.html": _shell("<h1>Introduction</h1><p>Welcome.</p>"),
        f"{_ROOT}cli/index.html": _shell("<h1>Command-line tool</h1>", ptr="../"),
        f"{_ROOT}faq.html": _shell("<h1>FAQ</h1><p>Answers.</p>"),
    }
    seen = _serve(monkeypatch, pages)

    acq = _mdbook.acquire(_ROOT, tmp_path, slug="books", title=None)

    assert acq.pages == 4
    assert f"{_ROOT}faq.html" in seen
    assert _headings(_staged(acq)[2])[0] == (2, "The build command")


def test_a_print_page_without_a_toc_stages_flat(tmp_path, monkeypatch):
    pages = {k: v for k, v in _BOOK.items() if not k.endswith("toc.html")}
    _serve(monkeypatch, pages, missing=(f"{_ROOT}toc.html",))

    acq = _mdbook.acquire(_ROOT, tmp_path, slug="books", title=None)

    assert acq.pages == 4
    assert [_headings(f)[0][0] for f in _staged(acq)] == [1, 1, 1, 1]


def test_no_print_page_and_no_toc_is_refused(tmp_path, monkeypatch):
    _serve(monkeypatch, {_ROOT: _BOOK[_ROOT]}, missing=(f"{_ROOT}toc.html", f"{_ROOT}print.html"))

    with pytest.raises(InvalidInputError):
        _mdbook.acquire(_ROOT, tmp_path, slug="books", title=None)


def test_a_soft_404_print_page_is_not_the_book(tmp_path, monkeypatch):
    """A host that answers every path with its home page is not serving print.html."""
    pages = {
        **_BOOK,
        f"{_ROOT}print.html": "<html><body><main><h1>Home</h1></main></body></html>",
        f"{_ROOT}index.html": _shell("<h1>Introduction</h1>"),
        f"{_ROOT}cli/index.html": _shell("<h1>Command-line tool</h1>", ptr="../"),
        f"{_ROOT}faq.html": _shell("<h1>FAQ</h1>"),
    }
    _serve(monkeypatch, pages)

    acq = _mdbook.acquire(_ROOT, tmp_path, slug="books", title=None)

    assert "Home" not in "\n".join(_staged(acq))
    assert acq.pages == 4


# --- the TOC-ordered crawl --------------------------------------------------

_SITE = "https://site.test/docs/"


def _sidebar(ptr: str) -> str:
    """An inline sidebar as older themes render it: page-relative hrefs, and each
    section list in a sibling <li> after its parent chapter."""
    return f"""<div class="sidebar-scrollbox"><ol class="chapter">
<li class="chapter-item expanded affix "><li class="part-title">Welcome</li>
<li class="chapter-item expanded "><a href="{ptr}getting-started.html">Getting Started</a></li>
<li class="chapter-item expanded "><a href="{ptr}installation.html">Installation</a></li>
<li><ol class="section"><li class="chapter-item expanded "><a href="{ptr}update.html">Update</a></li></ol></li>
<li class="chapter-item expanded "><a href="{ptr}ai/overview.html">AI</a></li>
</ol></div>"""


def _site_page(heading: str, *, ptr: str = "") -> str:
    main = (
        f"<h1>{heading}</h1><p>{heading} body.</p><h2>Details</h2>"
        '<div class="footer-buttons"><a rel="next" href="x.html">NEXTBUTTON</a></div>'
        '<footer class="footer"><a href="/">SITEFOOTER</a></footer>'
    )
    return _shell(main, ptr=ptr, title=f"{heading} | Site", sidebar=_sidebar(ptr))


_SITE_PAGES = {
    _SITE: _site_page("Getting Started"),
    f"{_SITE}getting-started.html": _site_page("Getting Started"),
    f"{_SITE}installation.html": _site_page("Installation"),
    f"{_SITE}update.html": _site_page("Update"),
    f"{_SITE}ai/overview.html": _site_page("AI", ptr="../"),
}


def test_without_a_print_page_the_toc_drives_a_crawl_in_order(tmp_path, monkeypatch):
    seen = _serve(monkeypatch, _SITE_PAGES, missing=(f"{_SITE}print.html",))

    acq = _mdbook.acquire(_SITE, tmp_path, slug="site", title="Getting Started | Site")

    assert acq.pages == 4
    assert seen[:2] == [_SITE, f"{_SITE}print.html"]
    assert seen[2:] == [
        f"{_SITE}getting-started.html",
        f"{_SITE}installation.html",
        f"{_SITE}update.html",
        f"{_SITE}ai/overview.html",
    ]
    assert [_headings(f)[0] for f in _staged(acq)] == [
        (1, "Getting Started"),
        (1, "Installation"),
        (2, "Update"),
        (1, "AI"),
    ]
    assert _headings(_staged(acq)[2])[1] == (3, "Details")


def test_crawled_pages_drop_the_theme_footer_and_pager(tmp_path, monkeypatch):
    _serve(monkeypatch, _SITE_PAGES, missing=(f"{_SITE}print.html",))

    acq = _mdbook.acquire(_SITE, tmp_path, slug="site", title=None)
    merged = "\n".join(_staged(acq))

    assert "NEXTBUTTON" not in merged
    assert "SITEFOOTER" not in merged
    assert "PAGER" not in merged
    assert "Installation body." in merged


def test_a_nested_seed_resolves_its_page_relative_sidebar(tmp_path, monkeypatch):
    seen = _serve(monkeypatch, _SITE_PAGES, missing=(f"{_SITE}print.html",))

    acq = _mdbook.acquire(f"{_SITE}ai/overview.html", tmp_path, slug="site", title=None)

    assert acq.pages == 4
    assert f"{_SITE}print.html" in seen
    assert f"{_SITE}getting-started.html" in seen


def test_without_a_book_title_the_passed_title_and_host_slug_stand(tmp_path, monkeypatch):
    _serve(monkeypatch, _SITE_PAGES, missing=(f"{_SITE}print.html",))

    acq = _mdbook.acquire(_SITE, tmp_path, slug="site", title="Getting Started | Site")

    assert acq.slug == "site"
    assert acq.title == "Getting Started | Site"


def test_lost_pages_are_counted(tmp_path, monkeypatch):
    """A fetch error and a 200 page with no <main> both leave a chapter unstaged."""
    pages = {**_SITE_PAGES, f"{_SITE}update.html": "<html><body><p>moved</p></body></html>"}
    _serve(monkeypatch, pages, missing=(f"{_SITE}print.html", f"{_SITE}installation.html"))

    acq = _mdbook.acquire(_SITE, tmp_path, slug="site", title=None)

    assert acq.pages == 2
    assert acq.lost == 2


def test_a_chapter_redirecting_out_of_the_book_is_lost(tmp_path, monkeypatch):
    def fake(url, **kwargs):
        if url.endswith("print.html"):
            raise HTTPError(url, 404, "Not Found", {}, None)
        if url.endswith("update.html"):
            return "https://elsewhere.test/login", _site_page("Login")
        return url, _SITE_PAGES[url]

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _mdbook.acquire(_SITE, tmp_path, slug="site", title=None)

    assert acq.pages == 3
    assert acq.lost == 1
    assert "Login" not in "\n".join(_staged(acq))


def test_duplicate_content_is_staged_once(tmp_path, monkeypatch):
    """Two TOC entries resolving to one page (an alias, a redirect) are one chapter."""
    pages = {**_SITE_PAGES, f"{_SITE}update.html": _SITE_PAGES[f"{_SITE}installation.html"]}
    _serve(monkeypatch, pages, missing=(f"{_SITE}print.html",))

    acq = _mdbook.acquire(_SITE, tmp_path, slug="site", title=None)

    assert acq.pages == 3
    assert acq.lost == 0


def test_the_crawl_page_cap_truncates_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr(_mdbook, "_MAX_PAGES", 2)
    _serve(monkeypatch, _SITE_PAGES, missing=(f"{_SITE}print.html",))

    with capture_logs() as logs:
        acq = _mdbook.acquire(_SITE, tmp_path, slug="site", title=None)

    assert acq.pages == 2
    assert acq.truncated is True
    assert any(e["event"] == "mdbook.capped" for e in logs)


def test_stalled_crawl_stops_and_reports_truncated(tmp_path, monkeypatch):
    """Every chapter fetches fine but dedupes away, so nothing is ever staged."""
    same = _site_page("Same")
    pages = dict.fromkeys(_SITE_PAGES, same)
    clock = {"t": 0.0}
    monkeypatch.setattr(_mdbook.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(_mdbook.cfg, "CRAWL_STALL_AFTER_S", 6)

    def fake(url, **kwargs):
        clock["t"] += 5.0
        if url.endswith("print.html"):
            raise HTTPError(url, 404, "Not Found", {}, None)
        return url, pages[url]

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _mdbook.acquire(_SITE, tmp_path, slug="site", title=None)

    assert acq.truncated is True
    assert acq.pages == 1


# --- heading hierarchy ------------------------------------------------------


def _fixed(html: str, *, depth: int, name: str) -> str:
    section = BeautifulSoup(f"<section>{html}</section>", "html.parser").section
    assert section is not None
    _mdbook.fix_headings(section, depth=depth, name=name)
    return str(section)


def test_a_chapter_without_a_heading_gets_its_toc_name():
    fragment = _fixed("<p>Just prose.</p>", depth=1, name="Update")

    assert _headings(fragment) == [(2, "Update")]


def test_a_chapter_opening_below_h1_is_lifted_to_its_toc_level():
    fragment = _fixed("<h2>Top</h2><h3>Sub</h3>", depth=0, name="Top")

    assert _headings(fragment) == [(1, "Top"), (2, "Sub")]


def test_nothing_in_a_chapter_outranks_its_title():
    fragment = _fixed("<h2>Top</h2><h1>Stray</h1>", depth=1, name="Top")

    assert _headings(fragment) == [(2, "Top"), (2, "Stray")]


def test_deep_headings_stop_at_h6():
    fragment = _fixed("<h1>T</h1><h4>D</h4><h6>E</h6>", depth=3, name="T")

    assert _headings(fragment) == [(4, "T"), (6, "D"), (6, "E")]


def test_the_print_pages_added_title_gives_way_to_the_chapters_own():
    """The print page puts the TOC name as an <h1> over a chapter that opens below
    h1; kept, it pushes the whole chapter a level deeper than its own page."""
    chapter = (
        '<h1 id="what-is-ownership-1"><a href="#what-is-ownership-1" class="header">'
        "What is Ownership?</a></h1>"
        "<!-- Old headings. Do not remove or links may break. -->"
        '<p><a id="old-anchor"></a></p>'
        '<h2 id="what-is-ownership">What Is <code>Ownership</code>?</h2><p>Body.</p>'
        "<h3>The Stack</h3>"
    )

    fragment = _fixed(chapter, depth=1, name="What is Ownership?")

    assert _headings(fragment) == [(2, "What Is Ownership?"), (3, "The Stack")]
    assert 'id="old-anchor"' in fragment


@pytest.mark.parametrize(
    ("toc_name", "own"),
    [
        ("A - Keywords", "Appendix A: Keywords"),
        (
            "From Single-Threaded to Multithreaded Server",
            "From a Single-Threaded to a Multithreaded Server",
        ),
    ],
)
def test_a_longer_own_title_replaces_the_toc_name(toc_name, own):
    fragment = _fixed(f"<h1>{toc_name}</h1><h2>{own}</h2>", depth=1, name=toc_name)

    assert _headings(fragment) == [(2, own)]


@pytest.mark.parametrize(
    "chapter",
    [
        "<h1>Setup</h1><h2>Install</h2>",
        "<h1>Setup</h1><p>Intro.</p><h2>Setup</h2>",
        "<h1>Art</h1><h2>Starting out</h2>",
        "<h2>Setup</h2><h3>Setup</h3>",
    ],
)
def test_a_chapter_title_over_a_different_heading_stays(chapter):
    assert len(_headings(_fixed(chapter, depth=0, name="Setup"))) == 2


def test_toc_numbering_stays_out_of_an_inserted_heading(tmp_path, monkeypatch):
    main = f"<p>Intro text.</p>{_SEP}<p>CLI text.</p>{_SEP}<p>Build text.</p>{_SEP}<p>FAQ text.</p>"
    pages = {**_BOOK, f"{_ROOT}print.html": _shell(main, title="Widget Guide")}
    _serve(monkeypatch, pages)

    acq = _mdbook.acquire(_ROOT, tmp_path, slug="books", title=None)

    assert _headings(_staged(acq)[2]) == [(2, "build")]


# --- normalize --------------------------------------------------------------


def test_normalize_merges_the_chapters_in_book_order(tmp_path, monkeypatch):
    _serve(monkeypatch, _BOOK)
    acq = _mdbook.acquire(_ROOT, tmp_path, slug="books", title=None)

    out = DocsProbePattern().normalize(acq, tmp_path)
    text = out.read_text(encoding="utf-8")

    assert out.name == "books-widget-guide.html"
    assert "<title>Widget Guide</title>" in text
    order = [text.index(s) for s in ("Welcome.", "CLI overview.", "Builds it.", "Answers.")]
    assert order == sorted(order)
