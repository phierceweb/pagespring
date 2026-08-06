"""_asciidoctor — generator detection, same-dir crawl, container extraction."""

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.patterns import _asciidoctor

_BASE = "https://manual.yamaha.com/mi/bo/ead10/en"

_NAV = (
    '<div id="global-header">CHROME</div>'
    '<a href="../language.html">Language</a>'
    '<a href="./index.html">Top</a>'
    '<a href="01_feature_en.html">Features</a>'
    '<a href="02_setting_en.html">Quick Guide</a>'
    '<a href="https://elsewhere.test/other.html">Offsite</a>'
)


def _page(title: str, body: str, *, container: str = "contentOrg") -> str:
    """<main id="content"> wraps the search widget AND the renamed inner
    container — the nesting the id ordering exists for."""
    return f"""<!DOCTYPE html><html><head>
<meta content="Asciidoctor 1.5.6.1" name="generator"/><title>{title}</title></head>
<body><div id="header">hdr</div>
<main id="content">{_NAV}<div id="searchresults">No more search results.</div>
<div id="{container}"><div class="sect1 contentarea">{body}</div></div></main>
<div id="footer"><div id="copyright">(c) Yamaha</div></div>
<script src="s.js"></script></body></html>"""


_PAGES = {
    f"{_BASE}/index.html": _page("EAD10 Web Manual", "<p>Welcome to the manual.</p>"),
    f"{_BASE}/01_feature_en.html": _page(
        "What is the EAD10?",
        '<h2>Features</h2><p>It senses.</p><img src="images/panel.svg"/>',
    ),
    f"{_BASE}/02_setting_en.html": _page("Quick Guide", "<h2>Setup</h2><p>Plug it in.</p>"),
}


@pytest.fixture
def fetched(monkeypatch):
    seen: list[str] = []

    def fake(url, **kwargs):
        seen.append(url)
        if url not in _PAGES:
            raise AssertionError(f"unexpected fetch: {url}")
        return url, _PAGES[url]

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    return seen


def test_crawls_every_same_directory_page(tmp_path, fetched):
    acq = _asciidoctor.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)

    assert acq.pages == 3
    assert f"{_BASE}/01_feature_en.html" in fetched
    assert f"{_BASE}/02_setting_en.html" in fetched


def test_does_not_leave_the_base_directory(tmp_path, fetched):
    """The nav links a parent-dir language switcher and an offsite page; both
    would drag in unrelated documents."""
    _asciidoctor.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)

    assert not any("language.html" in u for u in fetched)
    assert not any("elsewhere.test" in u for u in fetched)


def test_repeated_nav_does_not_refetch(tmp_path, fetched):
    """Every page carries the same nav — without dedup each page is fetched
    once per link that points at it."""
    _asciidoctor.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)

    assert len(fetched) == len(set(fetched)) == 3


def test_extracts_content_and_drops_chrome(tmp_path, fetched):
    _asciidoctor.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)
    merged = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((tmp_path / "raw").glob("*.html"))
    )

    assert "It senses." in merged
    assert "Plug it in." in merged
    assert "CHROME" not in merged
    assert "Yamaha" not in merged  # the footer copyright
    assert "s.js" not in merged


def test_image_refs_are_absolutized(tmp_path, fetched):
    _asciidoctor.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)
    merged = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((tmp_path / "raw").glob("*.html"))
    )

    assert f"{_BASE}/images/panel.svg" in merged


def test_standard_asciidoctor_content_id_also_works(tmp_path, monkeypatch):
    """Upstream emits `#content`; a retheme may rename it to `#contentOrg`.
    Supporting only one silently yields an empty deliverable."""
    pages = {f"{_BASE}/index.html": _page("Doc", "<p>Body.</p>", container="content")}
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, pages[url]))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _asciidoctor.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)

    assert acq.pages == 1
    assert "Body." in (tmp_path / "raw" / "0000-index.html").read_text(encoding="utf-8")


def test_missing_content_container_raises(tmp_path, monkeypatch):
    """No speculative selector ladder — fail loudly rather than stage a shell."""
    bare = (
        '<html><head><meta name="generator" content="Asciidoctor 1.5.6.1">'
        "</head><body><div id='mystery'>x</div></body></html>"
    )
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, bare))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    with pytest.raises(InvalidInputError):
        _asciidoctor.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)


def test_slug_combines_host_label_and_document_title(tmp_path, fetched):
    """One host serves many Asciidoctor manuals; the host label alone collides."""
    acq = _asciidoctor.acquire(f"{_BASE}/index.html", tmp_path, slug="yamaha", title=None)

    assert acq.slug == "yamaha-ead10-web-manual"


def test_a_dead_page_is_skipped_not_fatal(tmp_path, monkeypatch):
    def fake(url, **kwargs):
        if "02_setting" in url:
            raise OSError("500")
        return url, _PAGES[url]

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _asciidoctor.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)

    assert acq.pages == 2


def test_entry_page_leads_the_deliverable(tmp_path, fetched):
    """Discovery order is the author's reading order — the nav lists chapters in
    sequence, so a lexical sort of opaque filenames would scramble them."""
    _asciidoctor.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)
    first = sorted((tmp_path / "raw").glob("*.html"))[0].read_text(encoding="utf-8")

    assert "Welcome to the manual." in first


def test_docs_probe_routes_asciidoctor(tmp_path, fetched):
    """Routing lives in docs_probe's generator ladder — the module exports no
    detector of its own, so this is what proves an Asciidoctor site is claimed."""
    from pagespring.patterns.docs_probe import DocsProbePattern

    acq = DocsProbePattern().acquire(f"{_BASE}/index.html", tmp_path)

    assert acq.kind == "html"
    assert acq.pages == 3
    assert "Welcome to the manual." in (tmp_path / "raw" / "0000-index.html").read_text(
        encoding="utf-8"
    )


def test_inner_container_wins_over_the_stock_wrapper(tmp_path, fetched):
    """When the stock `#content` wraps the renamed `#contentOrg`, the more
    specific id must win — a grouped selector returns the wrapper and drags the
    search chrome in.
    """
    _asciidoctor.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)
    merged = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((tmp_path / "raw").glob("*.html"))
    )

    assert "No more search results." not in merged
    assert "searchresults" not in merged
    assert "Welcome to the manual." in merged


_SINGLE = """<html><head><meta name="generator" content="Asciidoctor 2.0.20"/>
<title>My Guide</title></head><body><div id="header"><h1>My Guide</h1></div>
<div id="content"><div class="sect1"><h2>Intro</h2><p>All of it, one file.</p></div></div>
<div id="footer">f</div></body></html>"""


def test_single_file_document_marks_itself_single_document(tmp_path, monkeypatch):
    """One self-contained HTML file is Asciidoctor's DEFAULT output, so it is the
    generator's most common shape — not a crawl that collapsed. Without this,
    every stock Asciidoctor manual fails `audit --all --strict`."""
    monkeypatch.setattr(http, "fetch_text", lambda u, **k: (u, _SINGLE))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _asciidoctor.acquire("https://ex.test/guide/index.html", tmp_path, slug="ex", title=None)

    assert acq.pages == 1
    assert acq.single_document is True


def test_a_collapsed_multipage_crawl_is_not_marked_single_document(tmp_path, monkeypatch):
    """The entry page DOES advertise siblings, but they all fail. That is the
    real collapse audit exists to catch — it must stay catchable."""

    def fake(url, **kwargs):
        if url.endswith("index.html"):
            return url, _PAGES[f"{_BASE}/index.html"]
        raise OSError("500")

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _asciidoctor.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)

    assert acq.pages == 1
    assert acq.single_document is False


def test_absent_cross_reference_does_not_defeat_single_document(tmp_path, monkeypatch):
    """A stock single-file manual still cross-references sibling docs, which may
    simply not be published on that host. Counting a 404 as a lost page marks
    every such manual as a collapsed crawl — the real Asciidoctor User Manual
    links two plugin docs that 404, and it is one self-contained file.
    """
    from urllib.error import HTTPError

    linked = _SINGLE.replace(
        '<div id="header">', '<a href="asciidoctor-maven-plugin.html">Maven</a><div id="header">'
    )

    def fake(url, **kwargs):
        if "maven" in url:
            raise HTTPError(url, 404, "Not Found", {}, None)
        return url, linked

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _asciidoctor.acquire("https://ex.test/guide/index.html", tmp_path, slug="ex", title=None)

    assert acq.pages == 1
    assert acq.single_document is True


def test_absent_cross_references_are_not_counted_as_lost(tmp_path, monkeypatch):
    """The same 404 siblings that must not defeat single_document must not count
    as lost either: a healthy one-file manual shipped lost=2, and audit failed it
    with pages_lost on a document that was complete."""
    from urllib.error import HTTPError

    linked = _SINGLE.replace(
        '<div id="header">',
        '<a href="asciidoctor-maven-plugin.html">Maven</a>'
        '<a href="asciidoctor-gradle-plugin.html">Gradle</a><div id="header">',
    )

    def fake(url, **kwargs):
        if "plugin" in url:
            raise HTTPError(url, 404, "Not Found", {}, None)
        return url, linked

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _asciidoctor.acquire("https://ex.test/guide/index.html", tmp_path, slug="ex", title=None)

    assert acq.pages == 1
    assert acq.lost == 0
    assert acq.single_document is True


def test_a_non_404_sibling_failure_is_still_counted_as_lost(tmp_path, monkeypatch):
    """Only "not published here" is exempt. A 500 is a page that exists and was
    missed — exempting it too would hide the throttling `lost` exists to catch."""
    from urllib.error import HTTPError

    linked = _SINGLE.replace('<div id="header">', '<a href="ch2.html">Two</a><div id="header">')

    def fake(url, **kwargs):
        if "ch2" in url:
            raise HTTPError(url, 500, "Server Error", {}, None)
        return url, linked

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _asciidoctor.acquire("https://ex.test/guide/index.html", tmp_path, slug="ex", title=None)

    assert acq.pages == 1
    assert acq.lost == 1
    assert acq.single_document is False


# --- seed normalization / crawl scoping ---


def test_directory_form_seed_scopes_to_that_directory(tmp_path, monkeypatch):
    """`.../en/` and `.../en` must scope to `.../en`, not its PARENT.

    Stripping unconditionally sent every chapter one level too high; they 404,
    pages collapses to 1, and single_document then suppresses the audit check
    that would have caught it."""
    seen: list[str] = []

    def fake(url, **kwargs):
        seen.append(url)
        key = url if url.endswith(".html") else f"{url.rstrip('/')}/index.html"
        if key not in _PAGES:
            from urllib.error import HTTPError

            raise HTTPError(url, 404, "Not Found", {}, None)
        return url, _PAGES[key]

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _asciidoctor.acquire(_BASE, tmp_path, slug="x", title=None)

    assert acq.pages == 3, f"crawled: {seen}"
    assert acq.single_document is False


def test_directory_seed_resolves_assets_inside_the_directory(tmp_path, monkeypatch):
    """page_url is also the urljoin base for absolutize_refs, so a suffix-less
    seed must carry a trailing slash or every relative asset resolves to the
    parent and 404s — with audit silent about it."""

    def fake(url, **kwargs):
        key = url if url.endswith(".html") else f"{url.rstrip('/')}/index.html"
        return url, _PAGES.get(key, _PAGES[f"{_BASE}/01_feature_en.html"])

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    _asciidoctor.acquire(_BASE, tmp_path, slug="x", title=None)
    merged = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((tmp_path / "raw").glob("*.html"))
    )

    assert f"{_BASE}/images/panel.svg" in merged


def test_host_root_seed_does_not_produce_a_malformed_base(tmp_path, monkeypatch):
    """A bare host root has no directory to strip; `rsplit` yielded "https:/"."""
    root = "https://docs.example.test"
    monkeypatch.setattr(http, "fetch_text", lambda u, **k: (u, _SINGLE))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _asciidoctor.acquire(root + "/", tmp_path, slug="x", title=None)

    assert acq.pages == 1
    assert _asciidoctor._base_dir(root + "/") == root


def test_entry_redirect_reanchors_the_crawl(tmp_path, monkeypatch):
    """Every page but the prefetched entry rebinds page_url from the fetch. A
    seed that redirects into a locale dir left base_dir, link resolution and
    asset URLs anchored to the pre-redirect URL."""
    flat = "https://manual.yamaha.com/mi/bo/ead10/index.html"

    def fake(url, **kwargs):
        if url == flat:
            return f"{_BASE}/index.html", _PAGES[f"{_BASE}/index.html"]
        return url, _PAGES[url]

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _asciidoctor.acquire(flat, tmp_path, slug="x", title=None)

    assert acq.pages == 3


def test_link_redirecting_out_of_the_manual_is_dropped(tmp_path, monkeypatch):
    """base_dir was checked on the href only, never re-checked after the fetch,
    so a same-dir link that 301s elsewhere had its content staged in."""
    outsider = "https://manual.yamaha.com/other/promo.html"

    def fake(url, **kwargs):
        if url == f"{_BASE}/02_setting_en.html":
            return outsider, _page("Promo", "<p>BUY NOW</p>")
        return url, _PAGES[url]

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    _asciidoctor.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)
    merged = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((tmp_path / "raw").glob("*.html"))
    )

    assert "BUY NOW" not in merged


def test_two_links_redirecting_to_one_page_stage_it_once(tmp_path, monkeypatch):
    """`seen` held pre-redirect URLs only, so aliases duplicated the page."""

    def fake(url, **kwargs):
        if url.endswith(("01_feature_en.html", "02_setting_en.html")):
            return f"{_BASE}/01_feature_en.html", _PAGES[f"{_BASE}/01_feature_en.html"]
        return url, _PAGES[url]

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _asciidoctor.acquire(f"{_BASE}/index.html", tmp_path, slug="x", title=None)

    assert acq.pages == 2  # index + the one real target, not three


def test_fragment_on_the_seed_does_not_double_stage_the_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(
        http, "fetch_text", lambda u, **k: (u.split("#")[0], _PAGES[u.split("#")[0]])
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _asciidoctor.acquire(f"{_BASE}/index.html#top", tmp_path, slug="x", title=None)

    assert acq.pages == 3


def test_seed_with_a_query_string_stays_well_formed():
    """The directory slash must go on the path, not after the query."""
    assert _asciidoctor._normalize_seed("https://h.test/docs?lang=en") == (
        "https://h.test/docs/?lang=en"
    )
    assert _asciidoctor._normalize_seed("https://h.test/docs/i.html?v=2") == (
        "https://h.test/docs/i.html?v=2"
    )


def test_sibling_that_fetches_but_cannot_extract_is_still_a_live_sibling(tmp_path, monkeypatch):
    """A page that returns 200 but whose container is gone (theme change) proves
    the document IS multipage. Counting only *staged* siblings marked it
    single_document, which suppresses audit's single_page_crawl — the same
    silent-collapse class the flag already caused once.
    """
    good = (
        '<html><head><meta name="generator" content="Asciidoctor 2.0"/><title>G</title>'
        '</head><body><a href="ch2.html">Two</a>'
        '<div id="content"><h2>One</h2><p>a</p></div></body></html>'
    )
    no_container = (
        '<html><head><meta name="generator" content="Asciidoctor 2.0"/></head>'
        '<body><div id="mystery">theme changed</div></body></html>'
    )

    def fake(url, **kwargs):
        return url, (good if url.endswith(("index.html", "/")) else no_container)

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _asciidoctor.acquire("https://h.test/guide/index.html", tmp_path, slug="g", title=None)

    assert acq.pages == 1
    assert acq.single_document is False


def test_stalled_crawl_stops_and_reports_truncated(tmp_path, monkeypatch):
    """A crawl that keeps fetching but stops producing pages must bail, not spin.

    Every request returns 200 and nothing is slow, so no socket timeout applies;
    only progress separates a working crawl from a spinning one. Bailing with
    work still queued surfaces as truncated, which audit already fails.
    """
    # Every sibling resolves to byte-identical content, so after the entry page
    # the hash dedup drops all of them — fetching without ever staging.
    nav = "".join(f'<a href="ch{i:03d}.html">x</a>' for i in range(40))
    body = (
        '<html><head><meta name="generator" content="Asciidoctor 2.0"/><title>T</title>'
        f"</head><body>{nav}"
        '<div id="content"><h2>Same</h2><p>identical everywhere</p></div></body></html>'
    )

    clock = {"t": 0.0}
    monkeypatch.setattr(_asciidoctor.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(_asciidoctor.cfg, "CRAWL_STALL_AFTER_S", 30)

    def fetch(url, **kwargs):
        clock["t"] += 5.0  # each fetch costs 5s of wall clock
        return url, body

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _asciidoctor.acquire("https://h.test/guide/index.html", tmp_path, slug="g", title=None)

    assert acq.truncated is True, "a stalled crawl must report truncated"
    assert acq.pages == 1  # only the entry page ever staged
    assert len(list(acq.raw_dir.glob("*.html"))) == 1


def test_watchdog_disabled_lets_a_slow_but_progressing_crawl_finish(tmp_path, monkeypatch):
    """0 disables the guard; a crawl that keeps staging must never be cut short."""
    nav = "".join(f'<a href="ch{i:03d}.html">x</a>' for i in range(5))

    def page(marker: str) -> str:
        return (
            '<html><head><meta name="generator" content="Asciidoctor 2.0"/><title>T</title>'
            f"</head><body>{nav}"
            f'<div id="content"><h2>{marker}</h2><p>{marker}</p></div></body></html>'
        )

    clock = {"t": 0.0}
    monkeypatch.setattr(_asciidoctor.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(_asciidoctor.cfg, "CRAWL_STALL_AFTER_S", 0)

    def fetch(url, **kwargs):
        clock["t"] += 600.0  # far past any window, but every page is new
        return url, page(url.rsplit("/", 1)[-1])

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _asciidoctor.acquire("https://h.test/guide/index.html", tmp_path, slug="g", title=None)

    assert acq.truncated is False
    assert acq.pages == 6  # index + 5 chapters
