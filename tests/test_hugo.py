"""_hugo: sitemap found by walking up from the seed, content in ``<main>``, order from the theme
sidebar (mocked fetch)."""

import pytest
from pf_core.exceptions import ClientError, InvalidInputError

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
    """A site may keep its sitemap at the origin while the docs live in a subdirectory."""
    _acq, seen = _acquire(
        tmp_path,
        monkeypatch,
        "https://docs.example.com/prod/en/index.html",
        sitemap_at="https://docs.example.com/sitemap.xml",
    )
    sitemaps = [u for u in seen if u.endswith("sitemap.xml")]
    assert sitemaps[-1] == "https://docs.example.com/sitemap.xml"
    assert len(sitemaps) > 1  # walks up rather than guessing the origin outright


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
    assert acq.pages == 2  # prod index + alm; print and other/ are not pages of prod


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
        return url, _PAGE.replace("<h2>Auto-Level</h2>", f"<h2>{url}</h2>")

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
    kept = [u for u in locs if _hugo._is_content_page(u, base, {base})]
    assert kept == ["https://d.ex.com/ozone/", "https://d.ex.com/ozone/eq/"]


def test_a_tags_categories_or_print_page_below_the_site_root_is_kept():
    """Hugo generates taxonomy and print pages only at a site root; deeper down those
    names are ordinary topics."""
    root = "https://d.ex.com/ozone"
    locs = [
        "https://d.ex.com/ozone/hub-images/tags/",
        "https://d.ex.com/ozone/catalog/categories/",
        "https://d.ex.com/ozone/export/print/",
        "https://d.ex.com/ozone/tags/eq/",
    ]
    kept = [u for u in locs if _hugo._is_content_page(u, root, {root})]
    assert kept == locs[:3]


_CHILD_EN_TAXONOMY = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.example.com/prod/en/index.html</loc></url>
  <url><loc>https://docs.example.com/prod/en/alm/tags/index.html</loc></url>
  <url><loc>https://docs.example.com/prod/en/tags/eq/index.html</loc></url>
</urlset>"""


def test_each_child_sitemap_is_a_site_root_for_taxonomy_pages(tmp_path, monkeypatch):
    """A multilingual index roots each language at its child sitemap's directory."""

    def fetch(url, **kwargs):
        if url == "https://docs.example.com/prod/sitemap.xml":
            return url, _SITEMAP_INDEX.replace(
                "<sitemap><loc>https://docs.example.com/prod/de/sitemap.xml</loc></sitemap>", ""
            )
        if url == "https://docs.example.com/prod/en/sitemap.xml":
            return url, _CHILD_EN_TAXONOMY
        if url.endswith("sitemap.xml"):
            raise OSError(f"404 {url}")
        return url, _PAGE

    monkeypatch.setattr(http, "fetch_text", fetch)

    acq = _hugo.acquire("https://docs.example.com/prod/", tmp_path, slug="prod", title=None)

    staged = sorted(p.name for p in acq.raw_dir.glob("*.html"))
    assert staged == ["0000-prod-en-index.html", "0001-prod-en-alm-tags-index.html"]


def test_a_page_without_main_counts_as_lost(tmp_path, monkeypatch):
    """A 200 page with no <main> counts as lost, the same as a fetch error."""
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


@pytest.mark.parametrize(
    "error",
    [OSError("503 throttled"), ClientError("body over the cap", context={"max_bytes": 10})],
    ids=["fetch-error", "untrusted-body"],
)
def test_an_unreadable_child_sitemap_truncates_the_result(tmp_path, monkeypatch, error):
    """Pages behind a failed child sitemap are never discovered, so `lost` cannot
    count them one by one — only truncated can carry the loss."""

    def fetch(url, **kwargs):
        if url == "https://docs.example.com/prod/sitemap.xml":
            return url, _SITEMAP_INDEX
        if url == "https://docs.example.com/prod/en/sitemap.xml":
            return url, _CHILD_EN
        if url == "https://docs.example.com/prod/de/sitemap.xml":
            raise error
        if url.endswith("sitemap.xml"):
            raise OSError(f"404 {url}")
        return url, _PAGE

    monkeypatch.setattr(http, "fetch_text", fetch)

    acq = _hugo.acquire("https://docs.example.com/prod/", tmp_path, slug="prod", title=None)

    assert acq.pages == 2  # only the en child is readable
    assert acq.truncated is True
    assert acq.lost == 0


def test_a_dotted_version_dir_is_not_treated_as_a_filename():
    """Popping a dotted version dir as a filename scopes at the product and merges sibling versions."""
    assert _hugo._base_dir("https://help.ex.com/docs/ozone/11.0/") == (
        "https://help.ex.com/docs/ozone/11.0"
    )
    assert _hugo._base_dir("https://help.ex.com/docs/ozone/v2.1") == (
        "https://help.ex.com/docs/ozone/v2.1"
    )
    # a real filename is still stripped
    assert _hugo._base_dir("https://help.ex.com/docs/ozone/index.html") == (
        "https://help.ex.com/docs/ozone"
    )


def _docsy_page(body_class="td-section", main='<div class="td-content"><h1>T</h1></div>'):
    return (
        f'<html><body class="{body_class}"><header><nav class="td-navbar"></nav></header>'
        '<div class="container-fluid td-outer"><div class="td-main">'
        '<aside class="td-sidebar"><nav class="td-sidebar-nav"></nav></aside>'
        f"<main>{main}</main></div></div></body></html>"
    )


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        (_docsy_page(), True),
        (_docsy_page(body_class="td-page"), True),
        (
            '<html><body class="td-home"><div class="td-outer">'
            '<main class="td-main"></main></div></body></html>',
            True,
        ),
        ('<html><body class="td-section"><main></main></body></html>', False),
        ('<html><body class="book-kind-page"><div class="td-main"></div></body></html>', False),
        ('<html><body><main><article class="gdoc-markdown"></article></main></body></html>', False),
    ],
    ids=["section", "page", "home", "no-layout", "other-body-class", "geekdoc"],
)
def test_is_docsy_reads_the_body_kind_and_the_td_main_layout(html, expected):
    assert _hugo.is_docsy(html) is expected


_ROOT = "https://docs.example.com/docs"


def _sitemap(*paths):
    urls = "".join(f"<url><loc>{_ROOT}{p}</loc></url>" for p in paths)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    )


def _sidebar_page(title, nav_paths):
    links = "".join(f'<a class="td-sidebar-link" href="/docs{p}">{p}</a>' for p in nav_paths)
    return _docsy_page(main=f'<div class="td-content"><h1>{title}</h1></div>').replace(
        '<nav class="td-sidebar-nav"></nav>', f'<nav class="td-sidebar-nav">{links}</nav>'
    )


def _crawl(tmp_path, monkeypatch, sitemap, pages):
    """Acquire ``_ROOT`` with ``pages`` mapping a path under it to (final path, body)."""
    fetched = []

    def fetch(url, **kwargs):
        if url == f"{_ROOT}/sitemap.xml":
            return url, sitemap
        if url.endswith("sitemap.xml"):
            raise OSError(f"404 {url}")
        fetched.append(url)
        final, body = pages[url[len(_ROOT) :]]
        return _ROOT + final, body

    monkeypatch.setattr(http, "fetch_text", fetch)
    acq = _hugo.acquire(_ROOT, tmp_path, slug="docs", title=None)
    staged = [
        p.read_text(encoding="utf-8").split(" -->", 1)[0].removeprefix(f"<!-- source: {_ROOT}")
        for p in sorted(acq.raw_dir.glob("*.html"))
    ]
    return acq, staged, fetched


def test_pages_are_staged_in_sidebar_order_not_sitemap_order(tmp_path, monkeypatch):
    """Hugo's sitemap is not a reading order; the theme's sidebar is."""
    nav = ["/", "/start/", "/start/install/", "/config/"]
    pages = {p: (p, _sidebar_page(p, nav)) for p in nav}
    sitemap = _sitemap("/start/install/", "/config/", "/", "/start/")

    acq, staged, _fetched = _crawl(tmp_path, monkeypatch, sitemap, pages)

    assert staged == nav
    assert acq.pages == 4
    assert [p.name for p in sorted(acq.raw_dir.glob("*.html"))][0] == "0000-docs.html"


def test_a_page_at_a_long_path_stages_under_a_short_file_name(tmp_path, monkeypatch):
    long_path = "/" + "a" * 300 + "/"  # past a file name's 255-byte limit
    plain = "<html><body><main><h1>{}</h1></main></body></html>"
    pages = {p: (p, plain.format(p)) for p in ("/", long_path)}

    acq, staged, _fetched = _crawl(tmp_path, monkeypatch, _sitemap("/", long_path), pages)

    assert staged == ["/", long_path]
    assert max(len(p.name) for p in acq.raw_dir.iterdir()) <= 100


def test_without_a_sidebar_the_sitemap_order_stands(tmp_path, monkeypatch):
    plain = "<html><body><main><h1>{}</h1></main></body></html>"
    paths = ["/b/", "/a/", "/"]
    pages = {p: (p, plain.format(p)) for p in paths}

    _acq, staged, _fetched = _crawl(tmp_path, monkeypatch, _sitemap(*paths), pages)

    assert staged == paths


def test_the_fullest_sidebar_sets_the_order(tmp_path, monkeypatch):
    """A compact sidebar lists only the current page's branch; the fullest one seen wins."""
    full = ["/", "/a/", "/b/", "/c/"]
    pages = {
        "/c/": ("/c/", _sidebar_page("c", ["/c/"])),
        "/b/": ("/b/", _sidebar_page("b", full)),
        "/a/": ("/a/", _sidebar_page("a", ["/a/"])),
        "/": ("/", _sidebar_page("root", ["/"])),
    }

    _acq, staged, _fetched = _crawl(
        tmp_path, monkeypatch, _sitemap("/c/", "/b/", "/a/", "/"), pages
    )

    assert staged == full


def test_a_capped_crawl_keeps_the_first_pages_in_reading_order(tmp_path, monkeypatch):
    """The cap applies after sidebar ordering, so a capped crawl keeps the manual's
    opening pages rather than whichever the sitemap listed first."""
    monkeypatch.setattr(_hugo, "_MAX_PAGES", 2)
    nav = ["/", "/a/", "/b/", "/c/"]
    pages = {p: (p, _sidebar_page(p, nav)) for p in nav}
    pages[""] = pages["/"]

    acq, staged, fetched = _crawl(tmp_path, monkeypatch, _sitemap("/c/", "/b/", "/a/", "/"), pages)

    assert staged == ["/", "/a/"]
    assert acq.truncated is True
    assert len(fetched) == len(set(fetched))


def test_a_capped_crawl_takes_its_order_from_a_page_past_a_landing_page(tmp_path, monkeypatch):
    """A theme's landing page may render no sidebar; the first sitemap pages carry one."""
    monkeypatch.setattr(_hugo, "_MAX_PAGES", 2)
    nav = ["/a/", "/b/", "/c/", "/d/"]
    pages = {p: (p, _sidebar_page(p, nav)) for p in nav}
    landing = "<html><body><main><h1>Welcome</h1></main></body></html>"
    pages[""] = pages["/"] = ("/", landing)

    acq, staged, _fetched = _crawl(
        tmp_path, monkeypatch, _sitemap("/d/", "/c/", "/", "/b/", "/a/"), pages
    )

    assert staged == ["/", "/a/"]  # the landing page leads the section it heads
    assert acq.truncated is True


def test_a_capped_crawl_without_an_entry_sidebar_keeps_sitemap_order(tmp_path, monkeypatch):
    monkeypatch.setattr(_hugo, "_MAX_PAGES", 2)
    plain = "<html><body><main><h1>{}</h1></main></body></html>"
    paths = ["/b/", "/a/", "/"]
    pages = {p: (p, plain.format(p)) for p in paths}
    pages[""] = pages["/"]

    acq, staged, _fetched = _crawl(tmp_path, monkeypatch, _sitemap(*paths), pages)

    assert staged == ["/b/", "/a/"]
    assert acq.truncated is True


def test_a_whole_section_book_view_is_skipped_not_lost(tmp_path, monkeypatch):
    """Hugo Book's ``layout: book`` renders every subsection into one page; staging it
    would duplicate the whole manual."""
    book_view = (
        '<html><body class="book-kind-section book-layout-book"><main>'
        "<article><h1>Intro</h1><h1>Setup</h1></article></main></body></html>"
    )
    page = "<html><body class='book-kind-page'><main><h1>{}</h1></main></body></html>"
    pages = {"/": ("/", book_view), "/intro/": ("/intro/", page.format("Intro"))}

    acq, staged, _fetched = _crawl(tmp_path, monkeypatch, _sitemap("/", "/intro/"), pages)

    assert staged == ["/intro/"]
    assert acq.pages == 1
    assert acq.lost == 0


def test_a_page_with_nothing_to_read_is_skipped_not_lost(tmp_path, monkeypatch):
    """A Hugo Book section with no body of its own renders an empty article."""
    empty = (
        '<html><body class="book-kind-section"><main><div class="book-page">'
        '<article class="markdown book-article"></article></div></main></body></html>'
    )
    page = "<html><body class='book-kind-page'><main><h1>{}</h1></main></body></html>"
    pages = {"/": ("/", empty), "/intro/": ("/intro/", page.format("Intro"))}

    acq, staged, _fetched = _crawl(tmp_path, monkeypatch, _sitemap("/", "/intro/"), pages)

    assert staged == ["/intro/"]
    assert acq.pages == 1
    assert acq.lost == 0


def test_one_page_under_two_urls_is_staged_once(tmp_path, monkeypatch):
    """A directory URL and its index.html are one page, as is a sitemap entry that
    redirects to another."""
    body = "<html><body><main><h1>Setup</h1><p>Real documentation.</p></main></body></html>"
    pages = {
        "/setup/": ("/setup/", body),
        "/setup/index.html": ("/setup/index.html", body),
        "/old-setup/": ("/setup/", body),
    }

    acq, staged, fetched = _crawl(
        tmp_path, monkeypatch, _sitemap("/setup/", "/setup/index.html", "/old-setup/"), pages
    )

    assert staged == ["/setup/"]
    assert acq.pages == 1
    assert acq.lost == 0
    assert f"{_ROOT}/setup/index.html" not in fetched


def _post_list(*paths):
    entries = "".join(
        f'<article class="gdoc-markdown gdoc-post"><header class="gdoc-post__header">'
        f'<h1 class="gdoc-post__title"><a href="/docs{p}">Post {p}</a></h1></header>'
        f"<section><p>Excerpt of {p}.</p></section></article>"
        for p in paths
    )
    return f'<html><body><main><div id="main-content">{entries}</div></main></body></html>'


_POST = "<html><body><main><article><h1>Post {0}</h1><p>Excerpt of {0}.</p></article></main></body></html>"


def test_a_list_page_is_skipped_when_every_page_it_lists_is_staged(tmp_path, monkeypatch):
    """A Hugo list page repeats the excerpts of the pages it indexes."""
    pages = {
        "/posts/": ("/posts/", _post_list("/posts/a/", "/posts/b/")),
        "/posts/a/": ("/posts/a/", _POST.format("/posts/a/")),
        "/posts/b/": ("/posts/b/", _POST.format("/posts/b/")),
    }

    acq, staged, _fetched = _crawl(
        tmp_path, monkeypatch, _sitemap("/posts/", "/posts/a/", "/posts/b/"), pages
    )

    assert staged == ["/posts/a/", "/posts/b/"]
    assert acq.pages == 2
    assert acq.lost == 0


def test_a_list_page_is_kept_when_a_page_it_lists_is_lost(tmp_path, monkeypatch):
    pages = {
        "/posts/": ("/posts/", _post_list("/posts/a/", "/posts/b/")),
        "/posts/a/": ("/posts/a/", _POST.format("/posts/a/")),
    }

    acq, staged, _fetched = _crawl(
        tmp_path, monkeypatch, _sitemap("/posts/", "/posts/a/", "/posts/b/"), pages
    )

    assert staged == ["/posts/", "/posts/a/"]
    assert acq.lost == 1
