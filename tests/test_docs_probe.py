"""docs_probe — generator sniffing + strategy dispatch, all http mocked."""

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns import _docusaurus, _mkdocs, _sphinx
from pagespring.patterns.docs_probe import DocsProbePattern

_MKDOCS_HOME = (
    '<html><head><meta name="generator" content="mkdocs-1.6.1"><title>M</title></head></html>'
)
_DOCUSAURUS_HOME = (
    '<html><head><meta name="generator" content="Docusaurus v3.8.1"><title>D</title></head></html>'
)
_SPHINX_HOME = (
    '<html><head><title>S</title><link href="_static/style.css"></head><body></body></html>'
)
_PLAIN_HOME = "<html><head><title>plain</title></head><body>nothing here</body></html>"


def _fake_acquire(kind):
    def fake(base_url, workdir, *, slug, title):
        raw = workdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        return AcquireResult(raw_dir=raw, kind=kind, slug=slug, pages=1, title=title)

    return fake


def test_match_claims_only_web_urls():
    p = DocsProbePattern()
    assert p.match("https://anything.example.com/some/docs")
    assert p.match("http://ex.com")
    assert not p.match("./local-openapi.json")
    assert not p.match("file:///tmp/x.html")


@pytest.mark.parametrize(
    ("home", "strategy_mod", "strategy_name"),
    [
        (_MKDOCS_HOME, _mkdocs, "mkdocs"),
        (_DOCUSAURUS_HOME, _docusaurus, "docusaurus"),
        (_SPHINX_HOME, _sphinx, "sphinx"),
    ],
)
def test_probe_dispatches_by_generator(tmp_path, monkeypatch, home, strategy_mod, strategy_name):
    monkeypatch.setattr(http, "fetch_text", lambda url, **k: (url, home))
    called = {}

    def spy(base_url, workdir, *, slug, title):
        called["strategy"] = strategy_name
        called["slug"] = slug
        return _fake_acquire("html")(base_url, workdir, slug=slug, title=title)

    monkeypatch.setattr(strategy_mod, "acquire", spy)
    acq = DocsProbePattern().acquire("https://docs.ex.org", tmp_path)
    assert called["strategy"] == strategy_name
    assert called["slug"] == "ex"
    assert acq.slug == "ex"


def test_probe_unrecognized_raises_invalid_input(tmp_path, monkeypatch):
    def fake_fetch(url, **kwargs):
        if url.endswith(("search/search_index.json", "llms.txt")):
            raise RuntimeError("404")
        return url, _PLAIN_HOME

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    with pytest.raises(InvalidInputError) as exc_info:
        DocsProbePattern().acquire("https://plain.example.com", tmp_path)
    assert "probed" in str(exc_info.value)


def test_normalize_markdown_concats_and_strips_banner(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "0000-a.md").write_text(
        "> For the complete documentation index, see [llms.txt](https://x/llms.txt). More.\n\n# A\n\nBody A.\n",
        encoding="utf-8",
    )
    (raw / "0001-b.md").write_text("# B\n\nBody B.\n", encoding="utf-8")
    acq = AcquireResult(raw_dir=raw, kind="markdown", slug="x", pages=2)
    out = DocsProbePattern().normalize(acq, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert out.name == "x.md"
    assert "For the complete documentation index" not in text
    assert text.index("# A") < text.index("# B")


def test_normalize_html_wraps_fragments_with_title(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "0000-a.html").write_text("<section><h1>A</h1></section>", encoding="utf-8")
    (raw / "0001-b.html").write_text("<section><h1>B</h1></section>", encoding="utf-8")
    acq = AcquireResult(raw_dir=raw, kind="html", slug="x", pages=2, title="X Manual")
    out = DocsProbePattern().normalize(acq, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert out.name == "x.html"
    assert "<title>X Manual</title>" in text
    assert text.index("<h1>A</h1>") < text.index("<h1>B</h1>")


def test_normalize_html_empty_raw_dir_writes_empty_file(tmp_path):
    """A zero-page crawl must write a 0-byte file, not a hollow shell — that's
    what trips orchestrate's EmptyOutputError before staging clobbers a prior
    good deliverable."""
    raw = tmp_path / "raw"
    raw.mkdir()
    acq = AcquireResult(raw_dir=raw, kind="html", slug="x", pages=0, title="X Manual")
    out = DocsProbePattern().normalize(acq, tmp_path)
    assert out.stat().st_size == 0


def test_normalize_html_escapes_title(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "0000-a.html").write_text("<section><h1>A</h1></section>", encoding="utf-8")
    acq = AcquireResult(raw_dir=raw, kind="html", slug="x", pages=1, title="Tips & <Tricks>")
    out = DocsProbePattern().normalize(acq, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "<title>Tips &amp; &lt;Tricks&gt;</title>" in text
