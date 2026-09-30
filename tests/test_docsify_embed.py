"""_docsify_embed — ``:include`` links replaced by the files they embed (mocked fetch)."""

import time

import pytest

from pagespring import http
from pagespring.patterns._docsify_embed import embed
from pagespring.patterns._docsify_routes import Site

_ROOT = "https://ex.test/"
_SITE = Site(root=_ROOT, homepage="README.md", alias=(), relative_links=False)
_DEMO = f"{_ROOT}demo.html"
_PAIR = "  <!-- /// [demo] -->\n  <p>{}</p>\n  <!-- /// [demo] -->\n"


def _serve(monkeypatch, text: str) -> None:
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, text))


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        (":omitFragmentLine", "```html\n<p>Shown.</p>\n```\n\n"),
        ("", "```html\n-->\n <p>Shown.</p>\n <!--\n```\n\n"),
    ],
)
def test_omit_fragment_line_drops_the_marker_lines_whole(monkeypatch, options, expected):
    _serve(monkeypatch, f"<body>\n{_PAIR.format('Shown.')}</body>\n")
    md = f"[demo]({_DEMO} ':include :type=code :fragment=demo {options}')\n"

    assert embed(md, "/", _SITE, {}) == expected


def test_omit_fragment_line_takes_the_first_pair_of_markers(monkeypatch):
    _serve(monkeypatch, _PAIR.format("First.") + _PAIR.format("Second."))
    md = f"[demo]({_DEMO} ':include :type=code :fragment=demo :omitFragmentLine')\n"

    assert embed(md, "/", _SITE, {}) == "```html\n<p>First.</p>\n```\n\n"


@pytest.mark.parametrize(
    "tail",
    ["/// [demo]\n", "/// [demo]"],
    ids=["closing-newline", "closing-at-eof"],
)
def test_omit_fragment_line_reads_a_long_line_in_linear_time(monkeypatch, tail):
    """A file with a long line (minified or generated source) must not stall the ingest."""
    _serve(monkeypatch, "x" * 40_000 + "\n/// [demo]\nkept();\n" + tail)
    md = f"[demo]({_DEMO} ':include :type=code :fragment=demo :omitFragmentLine')\n"

    start = time.perf_counter()
    out = embed(md, "/", _SITE, {})

    assert time.perf_counter() - start < 1
    assert out == "```html\nkept();\n```\n\n"
