"""_mediawiki — action-API acquisition of a seed page and the pages it links to (mocked fetch)."""

import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.patterns import _mediawiki
from pagespring.patterns.docs_probe import DocsProbePattern

_SEED = "https://wiki.example.org/wiki/Synth_Manual"
_API = "https://wiki.example.org/api.php"


def _head(*, generator=True, edit_uri="http://wiki.example.org/api.php?action=rsd", config=None):
    config = config or '{"wgNamespaceNumber":0,"wgPageName":"Synth_Manual","wgIsMainPage":!1}'
    gen = '<meta name="generator" content="MediaWiki 1.43.8">' if generator else ""
    rsd = f'<link rel="EditURI" type="application/rsd+xml" href="{edit_uri}">' if edit_uri else ""
    return (
        f"<!DOCTYPE html><html><head>{gen}{rsd}"
        f"<script>RLCONF={config};</script><title>Synth Manual - Example Wiki</title></head>"
        '<body><div id="mw-navigation">sidebar</div><div id="footer">skin footer</div></body></html>'
    )


def _parsed(title, text, links=()):
    return {"parse": {"title": title, "text": {"*": text}, "links": list(links)}}


def _link(title, ns=0, exists=True):
    return {"ns": ns, "*": title, **({"exists": ""} if exists else {})}


_SEED_TEXT = """<div class="mw-parser-output">
<div id="toc" class="toc"><ul><li><a href="#Setup">1 Setup</a></li></ul></div>
<h2><span class="mw-headline" id="Setup">Setup</span><span class="mw-editsection">[<a
 href="/index.php?title=Synth_Manual&amp;action=edit&amp;section=1">edit</a>]</span></h2>
<p>Read <a href="/wiki/Oscillators" title="Oscillators">the oscillators</a>, then
<a href="/wiki/Filters" class="mw-redirect" title="Filters">filters</a>, then
<a href="/wiki/Envelopes" title="Envelopes">envelopes</a>.</p>
<p><a href="/index.php?title=Missing&amp;action=edit&amp;redlink=1" class="new"
 title="Missing (page does not exist)">Missing</a>
<a href="/wiki/File:Panel.png" title="File:Panel.png">panel</a>
<a href="/wiki/Category:Synths" title="Category:Synths">synths</a>
<a href="https://en.wikipedia.org/wiki/Synth" class="extiw" title="wikipedia:Synth">wp</a></p>
<figure><a href="/wiki/File:Panel.png"><img src="/images/thumb/a/ab/Panel.png/500px-Panel.png"
 srcset="/images/thumb/a/ab/Panel.png/750px-Panel.png 1.5x, /images/thumb/a/ab/Panel.png/1000px-Panel.png 2x"></a></figure>
<script>tracker()</script>
</div>
<!--
NewPP limit report
-->"""

# The API lists links alphabetically; the rendered anchors carry the reading order.
_SEED_LINKS = [
    _link("Category:Synths", ns=14),
    _link("Envelopes"),
    _link("File:Panel.png", ns=6),
    _link("Filters"),
    _link("Missing", exists=False),
    _link("Oscillators"),
]

_API_PAGES = {
    "Synth_Manual": _parsed("Synth Manual", _SEED_TEXT, _SEED_LINKS),
    "Oscillators": _parsed(
        "Oscillators", '<div class="mw-parser-output"><p>Saw and square.</p></div>'
    ),
    "Filters": _parsed("Filter Section", '<div class="mw-parser-output"><p>Low-pass.</p></div>'),
    "Envelopes": _parsed("Envelopes", '<div class="mw-parser-output"><p>ADSR.</p></div>'),
}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def _serve(monkeypatch, api_pages, *, page=None):
    """Serve the seed HTML and ``action=parse`` answers keyed by the ``page`` parameter."""
    requested = []

    def fetch_text(url, **kwargs):
        if url.startswith(_API + "?"):
            query = parse_qs(urlparse(url).query)
            assert (query["action"], query["format"]) == (["parse"], ["json"])
            title = query["page"][0]
            requested.append(title)
            answer = api_pages[title]
            if isinstance(answer, Exception):
                raise answer
            return url, answer if isinstance(answer, str) else json.dumps(answer)
        assert url == _SEED, f"unexpected fetch: {url}"
        return url, page if page is not None else _head()

    monkeypatch.setattr(http, "fetch_text", fetch_text)
    return requested


def _acquire(tmp_path, monkeypatch, api_pages=None, *, page=None, slug="example"):
    requested = _serve(monkeypatch, api_pages or _API_PAGES, page=page)
    acq = _mediawiki.acquire(_SEED, tmp_path, slug=slug, title="Synth Manual - Example Wiki")
    return acq, requested


def _staged(acq):
    return [f.read_text(encoding="utf-8") for f in sorted(acq.raw_dir.glob("*.html"))]


def test_generator_meta_names_mediawiki():
    assert _mediawiki.is_mediawiki(_head())


def test_a_hardened_install_is_known_by_its_api_link_and_page_config():
    assert _mediawiki.is_mediawiki(_head(generator=False))


@pytest.mark.parametrize(
    "page",
    [
        _head(generator=False, edit_uri="https://blog.example.org/xmlrpc.php?rsd", config="{}"),
        _head(generator=False, edit_uri=None),
        _head(generator=False, config="{}"),
        "<html><head><title>Docs</title></head><body>plain</body></html>",
    ],
    ids=["wordpress-rsd", "no-api-link", "no-page-config", "plain"],
)
def test_other_sites_are_not_mediawiki(page):
    assert not _mediawiki.is_mediawiki(page)


def test_api_url_keeps_the_page_scheme_and_drops_the_query():
    assert _mediawiki.api_url(_SEED, _head()) == _API


def test_api_url_resolves_a_relative_link():
    page = _head(edit_uri="/w/api.php?action=rsd")
    assert _mediawiki.api_url(_SEED, page) == "https://wiki.example.org/w/api.php"


@pytest.mark.parametrize(
    ("config", "name"),
    [
        ('{"wgPageName":"Behringer_MOT\\u00d6R_61"}', "Behringer_MOTÖR_61"),
        ('{"wgPageName":"AC\\/DC"}', "AC/DC"),
    ],
)
def test_page_name_decodes_json_escapes(config, name):
    assert _mediawiki.page_name(_head(config=config)) == name


def test_page_name_reads_the_legacy_global():
    page = '<script>var wgPageName = "Main_Page";</script>'
    assert _mediawiki.page_name(page) == "Main_Page"


def test_seed_then_linked_pages_stage_in_reading_order(tmp_path, monkeypatch):
    acq, _ = _acquire(tmp_path, monkeypatch)
    staged = _staged(acq)
    assert [s.split("<h1>")[1].split("</h1>")[0] for s in staged] == [
        "Synth Manual",
        "Oscillators",
        "Filter Section",
        "Envelopes",
    ]
    assert (acq.pages, acq.lost, acq.truncated, acq.single_document) == (4, 0, False, False)
    assert staged[0].startswith(f"<!-- source: {_SEED} -->")
    assert staged[2].startswith(f"<!-- source: {_API[:-7]}index.php?title=Filter_Section -->")


def test_only_existing_pages_in_the_seed_namespace_are_followed(tmp_path, monkeypatch):
    _acq, requested = _acquire(tmp_path, monkeypatch)
    assert requested == ["Synth_Manual", "Oscillators", "Filters", "Envelopes"]


def test_a_help_namespace_seed_follows_its_own_namespace(tmp_path, monkeypatch):
    text = (
        '<p><a href="/wiki/Help:Mixer" title="Help:Mixer">mixer</a>'
        '<a href="/wiki/Talk:Mixer" title="Talk:Mixer">talk</a></p>'
    )
    pages = {
        "Help:Synth": _parsed(
            "Help:Synth", text, [_link("Help:Mixer", ns=12), _link("Talk:Mixer", ns=1)]
        ),
        "Help:Mixer": _parsed("Help:Mixer", "<p>Faders.</p>"),
    }
    config = '{"wgNamespaceNumber":12,"wgPageName":"Help:Synth"}'
    _acq, requested = _acquire(tmp_path, monkeypatch, pages, page=_head(config=config))
    assert requested == ["Help:Synth", "Help:Mixer"]


def test_a_listed_link_without_an_anchor_still_follows_the_anchored_ones(tmp_path, monkeypatch):
    text = '<p><a href="/wiki/Zeta" title="Zeta">z</a></p>'
    pages = {
        "Synth_Manual": _parsed("Synth Manual", text, [_link("Alpha"), _link("Zeta")]),
        "Zeta": _parsed("Zeta", "<p>Last letter.</p>"),
        "Alpha": _parsed("Alpha", "<p>First letter.</p>"),
    }
    _acq, requested = _acquire(tmp_path, monkeypatch, pages)
    assert requested == ["Synth_Manual", "Zeta", "Alpha"]


def test_a_redirect_alias_of_a_staged_page_stages_once(tmp_path, monkeypatch):
    text = (
        '<p><a href="/wiki/Filter_Section" title="Filter Section">a</a>'
        '<a href="/wiki/Filters" class="mw-redirect" title="Filters">b</a></p>'
    )
    body = "<p>Low-pass.</p>"
    pages = {
        "Synth_Manual": _parsed("Synth Manual", text, [_link("Filter Section"), _link("Filters")]),
        "Filter Section": _parsed("Filter Section", body),
        "Filters": _parsed("Filter Section", body),
    }
    acq, _ = _acquire(tmp_path, monkeypatch, pages)
    assert (acq.pages, acq.lost) == (2, 0)


def test_identical_content_under_two_titles_stages_once(tmp_path, monkeypatch):
    text = '<p><a href="/wiki/A" title="A">a</a><a href="/wiki/B" title="B">b</a></p>'
    pages = {
        "Synth_Manual": _parsed("Synth Manual", text, [_link("A"), _link("B")]),
        "A": _parsed("A", "<p>Same words.</p>"),
        "B": _parsed("B", "<p>Same words.</p>"),
    }
    acq, _ = _acquire(tmp_path, monkeypatch, pages)
    assert acq.pages == 2


def test_skin_and_parser_chrome_are_dropped(tmp_path, monkeypatch):
    acq, _ = _acquire(tmp_path, monkeypatch)
    seed = _staged(acq)[0]
    for chrome in ("toc", "mw-editsection", "NewPP", "tracker()", "sidebar", "skin footer"):
        assert chrome not in seed
    assert "Setup" in seed


def test_images_and_links_are_absolute_and_images_take_the_widest_rendition(tmp_path, monkeypatch):
    acq, _ = _acquire(tmp_path, monkeypatch)
    seed = _staged(acq)[0]
    assert 'src="https://wiki.example.org/images/thumb/a/ab/Panel.png/1000px-Panel.png"' in seed
    assert "srcset" not in seed
    assert 'href="https://wiki.example.org/wiki/Oscillators"' in seed


@pytest.mark.parametrize(
    "failure",
    [
        {"error": {"code": "missingtitle", "info": "The page you specified doesn't exist."}},
        HTTPError(_API, 503, "Service Unavailable", {}, None),
        "<html>not json</html>",
        _parsed("Oscillators", '<div class="mw-parser-output"><!-- empty --></div>'),
    ],
    ids=["api-error", "http-error", "not-json", "no-content"],
)
def test_a_linked_page_that_yields_nothing_counts_as_lost(tmp_path, monkeypatch, failure):
    acq, _ = _acquire(tmp_path, monkeypatch, {**_API_PAGES, "Oscillators": failure})
    assert (acq.pages, acq.lost) == (3, 1)


def test_links_past_the_cap_mark_the_crawl_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(_mediawiki, "_MAX_PAGES", 2)
    acq, requested = _acquire(tmp_path, monkeypatch)
    assert requested == ["Synth_Manual", "Oscillators"]
    assert (acq.pages, acq.truncated) == (2, True)


def test_a_seed_that_links_nowhere_is_one_document(tmp_path, monkeypatch):
    pages = {"Synth_Manual": _parsed("Synth Manual", "<p>Everything on one page.</p>")}
    acq, _ = _acquire(tmp_path, monkeypatch, pages)
    assert (acq.pages, acq.single_document) == (1, True)


@pytest.mark.parametrize(
    ("slug", "title", "expected"),
    [
        ("example", "Synth Manual", "example-synth-manual"),
        ("zynthian", "Zynthian UI User Guide - V5", "zynthian-ui-user-guide-v5"),
        ("mod", "Modulation Matrix", "mod-modulation-matrix"),
    ],
)
def test_slug_is_the_page_title_led_by_the_site(tmp_path, monkeypatch, slug, title, expected):
    pages = {"Synth_Manual": _parsed(title, "<p>Body.</p>")}
    acq, _ = _acquire(tmp_path, monkeypatch, pages, slug=slug)
    assert (acq.slug, acq.title) == (expected, title)


def test_a_seed_without_an_api_link_is_refused(tmp_path, monkeypatch):
    with pytest.raises(InvalidInputError, match="api.php"):
        _acquire(tmp_path, monkeypatch, page=_head(edit_uri=None))


def test_a_seed_the_api_refuses_is_refused(tmp_path, monkeypatch):
    pages = {"Synth_Manual": {"error": {"code": "missingtitle", "info": "gone"}}}
    with pytest.raises(InvalidInputError, match="missingtitle"):
        _acquire(tmp_path, monkeypatch, pages)


def test_normalize_joins_the_pages_into_one_document(tmp_path, monkeypatch):
    acq, _ = _acquire(tmp_path, monkeypatch)
    out = DocsProbePattern().normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert out.index("Saw and square.") < out.index("Low-pass.") < out.index("ADSR.")
    assert "<title>Synth Manual</title>" in out


def test_a_title_at_the_length_limit_still_stages(tmp_path, monkeypatch):
    long_title = "Signal Flow " * 21
    text = f'<p><a href="/wiki/Long" title="{long_title}">long</a></p>'
    pages = {
        "Synth_Manual": _parsed("Synth Manual", text, [_link(long_title)]),
        long_title: _parsed(long_title, "<p>Every route.</p>"),
    }
    acq, _ = _acquire(tmp_path, monkeypatch, pages)
    assert (acq.pages, acq.lost) == (2, 0)
