"""_wordpress — REST-link discovery + single-post acquire (mocked fetch)."""

import json

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.patterns import _wordpress

_POST_URL = "https://www.drumeo.com/beat/how-to-tune-drums/"
_REST_URL = "https://www.drumeo.com/beat/wp-json/wp/v2/posts/26372"

# WordPress declares the post's REST endpoint in the head. Reading it beats
# guessing a path: the install lives in a /beat/ subdirectory, not the root.
_PAGE = f"""<!DOCTYPE html><html><head>
<meta name="generator" content="WordPress 6.4.2" />
<link rel="alternate" type="application/json" href="{_REST_URL}" />
<title>How To Tune Your Drums</title></head>
<body><div class="white-box">theme chrome</div></body></html>"""

_REST = json.dumps(
    {
        "id": 26372,
        "slug": "how-to-tune-drums",
        "title": {"rendered": "How To Tune Your Drums"},
        "content": {
            "rendered": (
                '<div id="rank-math-toc"><nav><ul><li>Jump to a section</li></ul></nav></div>'
                "<h2>Why do we tune drums?</h2>"
                "<p></p><p></p>"
                '<p>Because a drum is a <a href="/beat/gear/">tuned instrument</a>.</p>'
                '<figure><img src="https://cdn.drumeo.com/head.jpg"/></figure>'
                "<script>track()</script>"
            )
        },
    }
)


def _fake_fetch(pages):
    def fetch_text(url, **kwargs):
        if url not in pages:
            raise AssertionError(f"unexpected fetch: {url}")
        return url, pages[url]

    return fetch_text


@pytest.fixture
def fetched(monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fake_fetch({_POST_URL: _PAGE, _REST_URL: _REST}))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def test_rest_link_is_read_from_the_page_head():
    assert _wordpress.rest_endpoint(_PAGE) == _REST_URL


def test_rest_link_absent_returns_none():
    assert _wordpress.rest_endpoint("<html><head><title>x</title></head></html>") is None


def test_acquires_rendered_content(tmp_path, fetched):
    acq = _wordpress.acquire(_POST_URL, tmp_path, slug="fallback", title=None)

    body = (tmp_path / "raw" / "0000.html").read_text(encoding="utf-8")
    assert "Why do we tune drums?" in body
    assert "tuned instrument" in body
    assert acq.pages == 1


def test_slug_comes_from_the_post_not_the_host(tmp_path, fetched):
    """One host serves many posts — a host slug would collide on every ingest."""
    acq = _wordpress.acquire(_POST_URL, tmp_path, slug="fallback", title=None)

    assert acq.slug == "how-to-tune-drums"


def test_title_comes_from_the_post(tmp_path, fetched):
    acq = _wordpress.acquire(_POST_URL, tmp_path, slug="fallback", title=None)

    assert acq.title == "How To Tune Your Drums"


def test_toc_nav_block_is_dropped(tmp_path, fetched):
    """Rank Math's TOC is on-page navigation, not manual content."""
    _wordpress.acquire(_POST_URL, tmp_path, slug="fallback", title=None)

    body = (tmp_path / "raw" / "0000.html").read_text(encoding="utf-8")
    assert "rank-math-toc" not in body
    assert "Jump to a section" not in body


def test_scripts_are_stripped_and_refs_absolutized(tmp_path, fetched):
    _wordpress.acquire(_POST_URL, tmp_path, slug="fallback", title=None)

    body = (tmp_path / "raw" / "0000.html").read_text(encoding="utf-8")
    assert "track()" not in body
    assert "https://www.drumeo.com/beat/gear/" in body
    assert "https://cdn.drumeo.com/head.jpg" in body


def test_empty_paragraph_runs_are_collapsed(tmp_path, fetched):
    _wordpress.acquire(_POST_URL, tmp_path, slug="fallback", title=None)

    body = (tmp_path / "raw" / "0000.html").read_text(encoding="utf-8")
    assert "<p></p>" not in body


def test_missing_rest_link_raises_invalid_input(tmp_path, monkeypatch):
    """No DOM fallback by design — a speculative selector ladder is exactly the
    drift-prone structure that rots silently when a theme changes."""
    bare = '<html><head><meta name="generator" content="WordPress 6.4.2"></head></html>'
    monkeypatch.setattr(http, "fetch_text", _fake_fetch({_POST_URL: bare}))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    with pytest.raises(InvalidInputError):
        _wordpress.acquire(_POST_URL, tmp_path, slug="fallback", title=None)


def test_unfetchable_rest_endpoint_raises_invalid_input(tmp_path, monkeypatch):
    def fetch_text(url, **kwargs):
        if url == _POST_URL:
            return url, _PAGE
        raise OSError("500")

    monkeypatch.setattr(http, "fetch_text", fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    with pytest.raises(InvalidInputError):
        _wordpress.acquire(_POST_URL, tmp_path, slug="fallback", title=None)


def test_marks_itself_a_single_document(tmp_path, fetched):
    """One post IS the deliverable, so `pages: 1` is correct — not the collapsed
    crawl that audit's single_page_crawl check exists to catch."""
    acq = _wordpress.acquire(_POST_URL, tmp_path, slug="fallback", title=None)

    assert acq.single_document is True


def test_nav_only_post_raises_rather_than_staging_an_empty_section(tmp_path, monkeypatch):
    """The guard must run AFTER cleaning. A post whose body is only a TOC block
    passes a raw-content check, then cleans to nothing — and single_document
    plus pages=1 switch off both of audit's content checks, so the hollow
    deliverable stages silently."""
    nav_only = json.dumps(
        {
            "slug": "empty",
            "title": {"rendered": "Empty"},
            "content": {
                "rendered": '<div id="rank-math-toc"><nav><ul><li>Top</li></ul></nav></div>'
            },
        }
    )
    monkeypatch.setattr(http, "fetch_text", _fake_fetch({_POST_URL: _PAGE, _REST_URL: nav_only}))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    with pytest.raises(InvalidInputError):
        _wordpress.acquire(_POST_URL, tmp_path, slug="fallback", title=None)


def test_paragraph_wrapped_embeds_survive_the_empty_collapse(tmp_path, monkeypatch):
    """`<p><iframe>` has no text and no img/br, so a naive emptiness test
    decomposes it — silently dropping every embedded video in the post."""
    embedded = json.dumps(
        {
            "slug": "embed",
            "title": {"rendered": "Embed"},
            "content": {
                "rendered": (
                    '<h2>Watch</h2><p><iframe src="https://player.example/1"></iframe></p><p></p>'
                )
            },
        }
    )
    monkeypatch.setattr(http, "fetch_text", _fake_fetch({_POST_URL: _PAGE, _REST_URL: embedded}))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    _wordpress.acquire(_POST_URL, tmp_path, slug="fallback", title=None)

    body = (tmp_path / "raw" / "0000.html").read_text(encoding="utf-8")
    assert "player.example/1" in body
    assert "<p></p>" not in body


def test_title_entities_are_decoded(tmp_path, monkeypatch):
    """WP's wptexturize emits `&#038;` for `&` in title.rendered; staged raw it
    reaches the deliverable heading literally."""
    entitled = json.dumps(
        {
            "slug": "care",
            "title": {"rendered": "Tuning &#038; Care"},
            "content": {"rendered": "<h2>Care</h2><p>Wipe it.</p>"},
        }
    )
    monkeypatch.setattr(http, "fetch_text", _fake_fetch({_POST_URL: _PAGE, _REST_URL: entitled}))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _wordpress.acquire(_POST_URL, tmp_path, slug="fallback", title=None)

    assert acq.title == "Tuning & Care"


_HARDENED = f"""<!DOCTYPE html><html><head>
<link rel="alternate" type="application/json" href="{_REST_URL}" />
<title>How To Tune Your Drums</title></head><body></body></html>"""


def test_detected_without_a_generator_meta():
    """Many WordPress installs strip the generator tag as hardening — wptavern
    and torquemag both do. The wp-json REST link the head declares is the
    reliable tell, and it is the same link acquire already reads."""
    assert _wordpress.is_wordpress(_HARDENED)


def test_detected_via_generator_meta():
    assert _wordpress.is_wordpress(_PAGE)


def test_not_detected_on_an_unrelated_json_alternate():
    """rel=alternate application/json is not WordPress-specific."""
    other = (
        '<html><head><link rel="alternate" type="application/json" '
        'href="https://x.test/api/v1/thing" /></head></html>'
    )

    assert not _wordpress.is_wordpress(other)


def test_not_detected_on_another_generator():
    assert not _wordpress.is_wordpress(
        '<html><head><meta name="generator" content="Hugo 0.1"></head></html>'
    )
