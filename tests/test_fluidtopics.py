"""_fluidtopics: Fluid Topics portals, read through the khub API the app shell loads (mocked fetch)."""

import base64
import json

import pytest
from pf_core.exceptions import InvalidInputError
from structlog.testing import capture_logs

from pagespring import http
from pagespring.config import cfg
from pagespring.patterns import _fluidtopics, _toc_stage
from pagespring.patterns.docs_probe import DocsProbePattern

_BASE = "https://docs.vendor.example/"
_API = f"{_BASE}api/khub/maps"

# Every portal URL answers with this shell; the content arrives through the API.
_SHELL = (
    '<!DOCTYPE html>\n<html lang="en-US"><head><meta name="ft-tenant-base-url"'
    f' content="{_BASE}"><title>Vendor</title>'
    f'<script src="{_BASE}scripts/fluidtopics.min.js?v=1"></script>'
    f'<script src="{_BASE}fluidtopicsclient.nocache.js?cb=2"></script></head>'
    '<body class="FT-version-5-3"><div id="FT-application-loader">Loading Website...</div>'
    "</body></html>"
)
_PNG = b"\x89PNG\r\n\x1a\nfirst"
_OTHER_PNG = b"\x89PNG\r\n\x1a\nsecond"
_THIRD_PNG = b"\x89PNG\r\n\x1a\nthird"


def _map(map_id: str, title: str, pretty: str, version: str) -> dict:
    meta = {"ft:prettyUrl": [pretty], "version": [version], "ft:locale": ["en-US"]}
    return {
        "title": title,
        "id": map_id,
        "mapApiEndpoint": f"/api/khub/maps/{map_id}",
        "metadata": [{"key": k, "label": k, "values": v} for k, v in meta.items()],
    }


_MAPS = [
    _map("ALL", "Widget Library", "widget-pro", "1.0"),
    _map("MAP15", "Widget Pro Help", "widget-pro/15.0/en", "15.0.30"),
    _map("MAP14", "Widget Pro Help", "widget-pro/14.0/en", "14.0.30"),
    _map("PLUG15", "Widget Pro Plug-in Reference", "widget-pro/plugref/15.0/en", "15.0.30"),
]


def _node(name: str, title: str, children: list | None = None) -> dict:
    return {
        "tocId": f"T-{name}",
        "contentId": f"C-{name}",
        "title": title,
        "prettyUrl": f"/r/widget-pro/15.0/en/topics/{name}.html",
        "hasRating": False,
        "children": children or [],
    }


_TOC = [
    _node("intro", "Introduction", [_node("setup", "Setting Up", [_node("wiring", "Wiring")])]),
    _node("mixing", "Mixing", [_node("busses", "Busses & Groups")]),
]


def _image(name: str, data: bytes) -> str:
    encoded = base64.b64encode(data).decode()
    return (
        f'<figure class="fig fignone"><img alt="" class="image" data-ft-asset-display-name="{name}"'
        f' data-ft-resource-id="R-{name}" src="data:image/png;base64,{encoded}"/></figure>'
    )


def _content(body: str) -> str:
    return (
        '<div class="content-locale-en-US content-locale-en"><div id="topic_c">'
        f'<div class="body conbody">{body}</div></div></div>'
    )


_TOPICS = {
    "intro": _content(
        '<p class="shortdesc">Intro text.</p><p class="p">Select <span class="ph menucascade">'
        '<strong class="ph uicontrol">File</strong><span aria-hidden="true"> &gt; </span>'
        '<span class="u-sr-only">and then</span><strong class="ph uicontrol">Open</strong>'
        "</span>.</p>" + _image("main_window.png", _PNG)
    ),
    "setup": _content(
        '<section class="section"><h2 class="title sectiontitle">Before You Start</h2>'
        '<p class="p">Setup body. See <span class="link ft-internal-link" data-mapid="MAP15"'
        ' data-tocid="T-wiring" title="Hover summary.">Wiring</span> and <span class="link'
        ' ft-internal-link" data-mapid="OTHER" data-tocid="T-elsewhere">Elsewhere</span>.</p>'
        "</section>"
    ),
    "wiring": _content('<p class="p">Wiring body.</p>' + _image("wiring_view.png", _PNG)),
    "mixing": '<div class="content-locale-en-US content-locale-en"></div>',
    "busses": _content(
        '<p class="p">Busses body.</p><script>track()</script>'
        + _image("main_window.png", _OTHER_PNG)
    ),
}


def _pages(topics: dict | None = None, toc: list | None = None) -> dict:
    pages = {
        f"{_BASE}r/widget-pro/15.0/en": _SHELL,
        f"{_BASE}r/widget-pro/15.0/en/topics/setup.html": _SHELL,
        _BASE: _SHELL,
        _API: json.dumps(_MAPS),
        f"{_API}/MAP15": json.dumps(_MAPS[1]),
        f"{_API}/MAP15/toc": json.dumps(_TOC if toc is None else toc),
    }
    for name, body in (_TOPICS if topics is None else topics).items():
        pages[f"{_API}/MAP15/topics/C-{name}/content"] = body
    return pages


def _serve(monkeypatch, pages, seen=None):
    seen = seen if seen is not None else []

    def fetch_text(url, **kwargs):
        seen.append(url)
        if url not in pages:
            raise OSError(f"404 {url}")
        return url, pages[url]

    monkeypatch.setattr(http, "fetch_text", fetch_text)
    return seen


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def _acquire(tmp_path, base=f"{_BASE}r/widget-pro/15.0/en", slug="vendor", title="Vendor"):
    return _fluidtopics.acquire(base, tmp_path, slug=slug, title=title)


def _staged(acq) -> list[str]:
    return [p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html"))]


# --- detection --------------------------------------------------------------


def test_recognises_the_app_shell_by_its_tenant_meta():
    assert _fluidtopics.is_fluidtopics(_SHELL)


def test_recognises_the_app_shell_by_its_tenant_meta_alone():
    shell = (
        f'<html><head><meta name="ft-tenant-base-url" content="{_BASE}"></head><body></body></html>'
    )

    assert _fluidtopics.is_fluidtopics(shell)


def test_recognises_the_app_shell_by_its_client_script_alone():
    shell = _SHELL.replace(f'<meta name="ft-tenant-base-url" content="{_BASE}">', "")

    assert _fluidtopics.is_fluidtopics(shell)


def test_the_portal_client_alone_tells_the_shell():
    page = '<html><head><script src="/fluidtopicsclient.nocache.js"></script></head></html>'

    assert _fluidtopics.is_fluidtopics(page)


@pytest.mark.parametrize("src", ["/scripts/fluidtopics.min.js?v=1", "/fluidtopics.js"])
def test_the_page_script_tells_the_shell_only_beside_its_app_loader(src):
    script = f'<script src="{src}"></script>'
    loader = '<div id="FT-application-loader">Loading Website...</div>'

    assert _fluidtopics.is_fluidtopics(f"<html><head>{script}</head><body>{loader}</body></html>")
    assert not _fluidtopics.is_fluidtopics(f"<html><head>{script}</head><body></body></html>")


@pytest.mark.parametrize(
    "page",
    [
        "<html><head><title>Docs</title></head><body><p>Hello</p></body></html>",
        "<html><body><p>We migrated from fluidtopics.min.js last year.</p></body></html>",
        '<html><head><meta name="tenant-base-url" content="https://x.example/"></head></html>',
        '<html><head><meta name="generator" content="Docusaurus">'
        '<script src="https://cdn.example/fluidtopics-search-widget.js"></script></head></html>',
        '<html><head><script src="/assets/fluidtopicsembed.min.js"></script></head></html>',
        '<html><head><meta name="generator" content="MkDocs">'
        '<script src="/assets/fluidtopics.min.js"></script></head><body><p>Docs</p></body></html>',
    ],
)
def test_rejects_pages_fluid_topics_did_not_write(page):
    assert not _fluidtopics.is_fluidtopics(page)


# --- the publication ----------------------------------------------------------


@pytest.mark.parametrize(
    ("seed", "map_id"),
    [
        (f"{_BASE}r/widget-pro/15.0/en", "MAP15"),
        (f"{_BASE}r/widget-pro/15.0/en/topics/setup.html", "MAP15"),
        (f"{_BASE}r/widget-pro/plugref/15.0/en", "PLUG15"),
        (f"{_BASE}reader/MAP15/T-setup", "MAP15"),
    ],
)
def test_the_seed_names_its_publication(tmp_path, monkeypatch, seed, map_id):
    pages = {**_pages(), seed: _SHELL, f"{_API}/{map_id}/toc": json.dumps([])}
    seen = _serve(monkeypatch, pages)

    with pytest.raises(InvalidInputError, match="no topics"):
        _acquire(tmp_path, base=seed)

    assert f"{_API}/{map_id}/toc" in seen


def test_a_percent_encoded_seed_names_its_publication_not_a_shorter_one(tmp_path, monkeypatch):
    maps = [*_MAPS, _map("MAPDE", "Widget Pro Hilfe", "widget-pro/15.0/de/übersicht", "15.0")]
    seed = f"{_BASE}r/widget-pro/15.0/de/%C3%BCbersicht"
    pages = {**_pages(), _API: json.dumps(maps), seed: _SHELL, f"{_API}/MAPDE/toc": "[]"}
    seen = _serve(monkeypatch, pages)

    with pytest.raises(InvalidInputError, match="no topics"):
        _acquire(tmp_path, base=seed)

    assert f"{_API}/MAPDE/toc" in seen


@pytest.mark.parametrize(
    "seed", ["r/widget-pro/16.0/en", "r/widget-pro/15.0/fr/", "r/widget-pro/manual.pdf"]
)
def test_a_seed_past_a_publication_naming_none_of_its_topics_is_refused(
    tmp_path, monkeypatch, seed
):
    """The longest pretty-URL prefix is then an umbrella publication, not the one asked for."""
    seed = f"{_BASE}{seed}"
    _serve(monkeypatch, {**_pages(), seed: _SHELL, f"{_API}/ALL/toc": json.dumps(_TOC)})

    with pytest.raises(InvalidInputError, match="names no topic of Widget Library"):
        _acquire(tmp_path, base=seed)


def test_a_seed_naming_an_umbrella_publication_itself_is_read(tmp_path, monkeypatch):
    seed = f"{_BASE}r/widget-pro/"
    pages = {k.replace("/MAP15/", "/ALL/"): v for k, v in _pages().items()}
    _serve(monkeypatch, {**pages, seed: _SHELL})

    assert _acquire(tmp_path, base=seed).pages == 4


def test_a_reader_id_url_reads_only_its_own_publication(tmp_path, monkeypatch):
    seed = f"{_BASE}reader/MAP15/T-setup"
    seen = _serve(monkeypatch, {**_pages(), seed: _SHELL})

    _acquire(tmp_path, base=seed)

    assert _API not in seen and f"{_API}/MAP15" in seen


@pytest.mark.parametrize(
    ("seed", "match"),
    [
        (_BASE, "names no publication"),
        (f"{_BASE}r/gadget/2.0/en", "names no publication"),
    ],
)
def test_a_seed_naming_no_publication_is_refused(tmp_path, monkeypatch, seed, match):
    _serve(monkeypatch, {**_pages(), seed: _SHELL})

    with pytest.raises(InvalidInputError, match=match):
        _acquire(tmp_path, base=seed)


# --- acquire ------------------------------------------------------------------


def test_topics_are_staged_in_toc_order_under_their_reader_urls(tmp_path, monkeypatch):
    _serve(monkeypatch, _pages())

    acq = _acquire(tmp_path)

    sources = [s.split("\n", 1)[0] for s in _staged(acq)]
    assert sources == [
        f"<!-- source: {_BASE}r/widget-pro/15.0/en/topics/{name}.html -->"
        for name in ("intro", "setup", "wiring", "busses")
    ]
    assert (acq.kind, acq.pages, acq.lost, acq.truncated) == ("html", 4, 0, False)
    assert [p.name for p in sorted(acq.raw_dir.glob("*.html"))][0] == "0000-topics-intro.html"


def test_the_toc_title_heads_each_topic_and_its_sections_sit_below(tmp_path, monkeypatch):
    _serve(monkeypatch, _pages())

    intro, setup, wiring, busses = _staged(_acquire(tmp_path))

    assert "<h1>Introduction</h1>" in intro
    assert "<h2>Setting Up</h2>" in setup and ">Before You Start</h3>" in setup
    assert "<h3>Wiring</h3>" in wiring
    assert busses.index("<h1>Mixing</h1>") < busses.index("<h2>Busses &amp; Groups</h2>")


def test_screen_reader_text_and_scripts_are_dropped(tmp_path, monkeypatch):
    _serve(monkeypatch, _pages())

    joined = "\n".join(_staged(_acquire(tmp_path)))

    assert "and then" not in joined and "track()" not in joined
    assert "File</strong>" in joined and "Open</strong>" in joined


def test_internal_links_point_at_reader_pages(tmp_path, monkeypatch):
    _serve(monkeypatch, _pages())

    setup = _staged(_acquire(tmp_path))[1]

    assert f'<a href="{_BASE}r/widget-pro/15.0/en/topics/wiring.html">Wiring</a>' in setup
    assert "Elsewhere" in setup and "T-elsewhere" not in setup
    assert "Hover summary" not in setup


def test_inline_images_are_bundled_once_per_distinct_image(tmp_path, monkeypatch):
    _serve(monkeypatch, _pages())

    acq = _acquire(tmp_path)

    intro, _setup, wiring, busses = _staged(acq)
    bundle = acq.raw_dir / "images"
    assert sorted(p.name for p in bundle.iterdir()) == ["main_window-2.png", "main_window.png"]
    assert (bundle / "main_window.png").read_bytes() == _PNG
    assert (bundle / "main_window-2.png").read_bytes() == _OTHER_PNG
    assert 'src="images/main_window.png"' in intro and 'src="images/main_window.png"' in wiring
    assert 'src="images/main_window-2.png"' in busses and "base64" not in busses


def test_an_inline_image_that_does_not_decode_is_dropped_loudly(tmp_path, monkeypatch):
    broken = _content('<p>Body.</p><img alt="" src="data:image/png;base64,@@@" />')
    _serve(monkeypatch, _pages(topics={**_TOPICS, "wiring": broken}))

    with capture_logs() as logs:
        wiring = _staged(_acquire(tmp_path))[2]

    assert "<img" not in wiring and "Body." in wiring
    assert any(e["event"] == "fluidtopics.image_undecodable" for e in logs)


def test_a_lazy_or_responsive_image_ref_is_made_absolute(tmp_path, monkeypatch):
    imgs = (
        '<img alt="" data-src="../media/lazy.png"/>'
        '<img alt="" src="../media/small.png" srcset="../media/big.png 800w"/>'
    )
    _serve(monkeypatch, _pages(topics={**_TOPICS, "wiring": _content(f"<p>Body.</p>{imgs}")}))

    wiring = _staged(_acquire(tmp_path))[2]

    assert f'src="{_BASE}r/widget-pro/15.0/en/media/lazy.png"' in wiring
    assert f'src="{_BASE}r/widget-pro/15.0/en/media/big.png"' in wiring


@pytest.mark.parametrize(
    ("src", "name", "data"),
    [
        (
            "data:image/png;charset=utf-8;base64," + base64.b64encode(_THIRD_PNG).decode(),
            "diagram.png",
            _THIRD_PNG,
        ),
        (
            "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg'/>",
            "diagram.svg",
            b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        ),
        ("data:image/svg+xml,%3Csvg%2F%3E", "diagram.svg", b"<svg/>"),
    ],
)
def test_inline_images_with_parameters_or_plain_text_are_bundled(
    tmp_path, monkeypatch, src, name, data
):
    img = f'<img alt="" data-ft-asset-display-name="diagram.x" src="{src}" />'
    _serve(monkeypatch, _pages(topics={**_TOPICS, "wiring": _content(f"<p>Body.</p>{img}")}))

    acq = _acquire(tmp_path)

    assert f'src="images/{name}"' in _staged(acq)[2]
    assert (acq.raw_dir / "images" / name).read_bytes() == data


def test_a_topic_that_fails_to_fetch_counts_as_lost(tmp_path, monkeypatch):
    topics = {k: v for k, v in _TOPICS.items() if k != "wiring"}
    _serve(monkeypatch, _pages(topics=topics))

    acq = _acquire(tmp_path)

    assert (acq.pages, acq.lost) == (3, 1)


def test_the_page_cap_truncates(tmp_path, monkeypatch):
    _serve(monkeypatch, _pages())
    monkeypatch.setattr(_fluidtopics, "_MAX_PAGES", 2)

    acq = _acquire(tmp_path)

    assert (acq.pages, acq.truncated) == (2, True)


def test_a_stalled_crawl_stops_and_reports_truncated(tmp_path, monkeypatch):
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(_toc_stage.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(cfg, "CRAWL_STALL_AFTER_S", 150)
    _serve(monkeypatch, _pages(topics={"intro": _TOPICS["intro"]}))

    acq = _acquire(tmp_path)

    assert acq.truncated is True and acq.pages == 1 and acq.lost < 4


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({_API: None}, "api/khub/maps"),
        ({_API: "<html>Not found</html>"}, "api/khub/maps"),
        ({f"{_API}/MAP15/toc": None}, "toc"),
        ({f"{_API}/MAP15/toc": '{"not": "a tree"}'}, "toc"),
        ({f"{_API}/MAP15/toc": "[" * 100_000 + "]" * 100_000}, "toc"),
    ],
)
def test_a_portal_that_cannot_list_its_topics_is_refused(tmp_path, monkeypatch, change, match):
    pages = {**_pages(), **change}
    _serve(monkeypatch, {k: v for k, v in pages.items() if v is not None})

    with pytest.raises(InvalidInputError, match=match):
        _acquire(tmp_path)


# --- identity -----------------------------------------------------------------


def test_slug_and_title_name_the_publication_and_its_version(tmp_path, monkeypatch):
    _serve(monkeypatch, _pages())

    acq = _acquire(tmp_path)

    assert (acq.slug, acq.title) == ("vendor-widget-pro-15-0-en", "Widget Pro Help 15.0.30")


# --- docs_probe -------------------------------------------------------------


def test_docs_probe_routes_a_reader_url_and_bundles_its_images(tmp_path, monkeypatch):
    _serve(monkeypatch, _pages())
    probe = DocsProbePattern()

    acq = probe.acquire(f"{_BASE}r/widget-pro/15.0/en/", tmp_path)
    out = probe.normalize(acq, tmp_path)

    assert (acq.slug, acq.pages) == ("vendor-widget-pro-15-0-en", 4)
    assert (out.parent / "images" / "main_window.png").read_bytes() == _PNG


def _under(nodes: list, prefix: str) -> list:
    return [
        {**n, "prettyUrl": prefix + n["prettyUrl"], "children": _under(n["children"], prefix)}
        for n in nodes
    ]


@pytest.mark.parametrize("toc_prefix", ["", "/help"], ids=["tenant_relative", "host_absolute"])
def test_a_portal_under_a_path_reads_the_api_beside_its_shell(tmp_path, monkeypatch, toc_prefix):
    base = "https://www.vendor.example/help/"
    seed = f"{base}r/widget-pro/15.0/en"
    shell = _SHELL.replace(f'content="{_BASE}"', f'content="{base}"')
    pages = {k.replace(_BASE, base): v for k, v in _pages(toc=_under(_TOC, toc_prefix)).items()}
    seen = _serve(monkeypatch, {**pages, seed: shell})

    acq = _acquire(tmp_path, base=seed)

    assert acq.pages == 4 and f"{base}api/khub/maps" in seen
    intro = f"{base}r/widget-pro/15.0/en/topics/intro.html"
    assert _staged(acq)[0].startswith(f"<!-- source: {intro} -->")
    assert [p.name for p in sorted(acq.raw_dir.glob("*.html"))][0] == "0000-topics-intro.html"


def test_a_topic_repeating_another_title_and_body_is_staged_once(tmp_path, monkeypatch):
    toc = json.loads(json.dumps(_TOC))
    toc[1]["children"][0]["title"] = "Wiring"
    _serve(monkeypatch, _pages(topics={**_TOPICS, "busses": _TOPICS["wiring"]}, toc=toc))

    acq = _acquire(tmp_path)

    assert (acq.pages, acq.lost) == (3, 0)


def test_content_reused_under_another_title_is_staged_under_each(tmp_path, monkeypatch):
    toc = json.loads(json.dumps(_TOC))
    toc[1]["children"].append({**_node("aux", "Aux Sends"), "contentId": "C-busses"})
    _serve(monkeypatch, _pages(toc=toc))

    aux = _staged(_acquire(tmp_path))[-1]

    assert aux.index("<h2>Aux Sends</h2>") < aux.index("Busses body.")


def test_a_title_only_toc_node_heads_the_topics_under_it(tmp_path, monkeypatch):
    toc = json.loads(json.dumps(_TOC))
    group = {"tocId": "T-ref", "title": "Reference", "children": [_node("busses", "Busses")]}
    toc[1]["children"] = [group, {"tocId": "T-empty", "title": "Nothing Here", "children": []}]
    _serve(monkeypatch, _pages(toc=toc))

    joined = "\n".join(_staged(_acquire(tmp_path)))

    assert joined.index("<h2>Reference</h2>") < joined.index("<h3>Busses</h3>")
    assert "Nothing Here" not in joined


def test_distinct_topics_sharing_a_body_both_keep_their_titles(tmp_path, monkeypatch):
    """A topic's body carries no title; the TOC is the only place the second one is named."""
    same = _content('<p class="p">See the parameter list.</p>')
    _serve(monkeypatch, _pages(topics={**_TOPICS, "wiring": same, "busses": same}))

    joined = "\n".join(_staged(_acquire(tmp_path)))

    assert "<h3>Wiring</h3>" in joined and "<h2>Busses &amp; Groups</h2>" in joined


@pytest.mark.parametrize(
    "body",
    [
        '<div class="content-locale-en-US content-locale-en"></div>',
        _content('<span class="u-sr-only">Opens in a new tab</span>'),
    ],
    ids=["empty", "screen_reader_only"],
)
def test_an_empty_topic_with_nothing_under_it_leaves_no_heading(tmp_path, monkeypatch, body):
    toc = json.loads(json.dumps(_TOC))
    toc[0]["children"].append(_node("stub", "Coming Soon"))
    _serve(monkeypatch, _pages(topics={**_TOPICS, "stub": body}, toc=toc))

    joined = "\n".join(_staged(_acquire(tmp_path)))

    assert "Coming Soon" not in joined and "<h1>Mixing</h1>" in joined


@pytest.mark.parametrize("skip", ["duplicate_body", "reused_topic", "fetch_fails"])
def test_a_skipped_topic_still_heads_the_topics_under_it(tmp_path, monkeypatch, skip):
    toc, topics = json.loads(json.dumps(_TOC)), dict(_TOPICS)
    if skip == "duplicate_body":
        toc[0]["title"], topics["setup"] = "Setting Up", topics["intro"]
    elif skip == "reused_topic":
        toc[0]["title"], toc[0]["children"][0]["contentId"] = "Setting Up", "C-intro"
    else:
        del topics["setup"]
    _serve(monkeypatch, _pages(topics=topics, toc=toc))

    wiring = next(s for s in _staged(_acquire(tmp_path)) if "Wiring body." in s)

    assert wiring.index("<h2>Setting Up</h2>") < wiring.index("<h3>Wiring</h3>")


def test_a_skipped_topic_with_nothing_under_it_leaves_no_heading(tmp_path, monkeypatch):
    toc = json.loads(json.dumps(_TOC))
    toc[0]["children"][0]["children"][0]["title"] = "Introduction"
    _serve(monkeypatch, _pages(topics={**_TOPICS, "wiring": _TOPICS["intro"]}, toc=toc))

    joined = "\n".join(_staged(_acquire(tmp_path)))

    assert "<h3>Introduction</h3>" not in joined and "<h1>Mixing</h1>" in joined


def test_a_link_to_a_section_keeps_its_anchor_and_a_node_without_a_reader_url_gets_one(
    tmp_path, monkeypatch
):
    toc = json.loads(json.dumps(_TOC))
    del toc[0]["children"][0]["children"][0]["prettyUrl"]
    setup = _TOPICS["setup"].replace(
        'data-tocid="T-wiring"', 'data-tocid="T-wiring" data-section="s1"'
    )
    _serve(monkeypatch, _pages(topics={**_TOPICS, "setup": setup}, toc=toc))

    staged = _staged(_acquire(tmp_path))

    assert f'href="{_BASE}reader/MAP15/T-wiring#s1"' in staged[1]
    assert staged[2].startswith(f"<!-- source: {_BASE}reader/MAP15/T-wiring -->")
