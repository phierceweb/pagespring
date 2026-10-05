"""readthedocs — match + PDF-build discovery with mocked http (no network)."""

import urllib.error

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns import _sphinx
from pagespring.patterns.readthedocs import ReadTheDocsPattern, _lang_version

_PDF = b"%PDF-1.4 fake body"


def test_match():
    p = ReadTheDocsPattern()
    assert p.match("https://requests.readthedocs.io/en/latest/")
    assert p.match("https://docs.readthedocs.io/en/stable/intro.html")
    assert p.match("https://requests.readthedocs.io")
    # Explicit download URLs keep routing to pdf_url (its /pdf carve-out).
    assert not p.match("https://requests.readthedocs.io/_/downloads/en/latest/pdf/")
    assert not p.match("https://requests.readthedocs.io/x/manual.pdf")
    # Any explicit RTD download URL is declined, not just the /pdf/ build.
    assert not p.match("https://requests.readthedocs.io/_/downloads/en/latest/htmlzip/")
    # Not RTD.
    assert not p.match("https://docs.python.org/3/")


@pytest.mark.parametrize(
    "url",
    [
        "https://foo.readthedocs.io/en/latest/_static/openapi.yaml",
        "https://foo.readthedocs.io/en/latest/_static/swagger.json",
        "https://foo.readthedocs.io/en/latest/_static/api.YML",
        "https://foo.readthedocs.io/en/latest/_downloads/abc123/examples.zip",
        "https://foo.readthedocs.io/en/latest/_downloads/abc123/source.tar.gz",
        "https://foo.readthedocs.io/en/latest/_downloads/abc123/guide.pdf",
    ],
)
def test_a_url_naming_a_file_is_declined(url):
    """The file named is the document; the project's PDF build is a different one."""
    assert not ReadTheDocsPattern().match(url)


def test_lang_version():
    assert _lang_version("/en/latest/") == ("en", "latest")
    assert _lang_version("/en/stable/intro.html") == ("en", "stable")
    assert _lang_version("/pt-br/v2.0/") == ("pt-br", "v2.0")
    assert _lang_version("/") == ("en", "latest")
    assert _lang_version("/intro.html") == ("en", "latest")


def test_subproject_acquire_downloads_the_subproject_build(tmp_path, monkeypatch):
    """RTD serves a subproject's build at /_/downloads/<alias>/<lang>/<version>/pdf/."""
    seen = {}

    def fake_fetch_bytes(url, **kwargs):
        seen["url"] = url
        return url, _PDF

    monkeypatch.setattr(http, "fetch_bytes", fake_fetch_bytes)
    acq = ReadTheDocsPattern().acquire(
        "https://parent.readthedocs.io/projects/child/en/stable/usage.html", tmp_path
    )
    assert seen["url"] == "https://parent.readthedocs.io/_/downloads/child/en/stable/pdf/"
    assert acq.slug == "parent-child"


@pytest.mark.parametrize(
    ("url", "download"),
    [
        (
            "https://parent.readthedocs.io/projects/child/",
            "https://parent.readthedocs.io/_/downloads/child/en/latest/pdf/",
        ),
        (
            "https://parent.readthedocs.io/projects/api/python/ja/v2.1/",
            "https://parent.readthedocs.io/_/downloads/api/python/ja/v2.1/pdf/",
        ),
        (
            "https://parent.readthedocs.io/projects/ui/en/latest/",
            "https://parent.readthedocs.io/_/downloads/ui/en/latest/pdf/",
        ),
    ],
)
def test_subproject_alias_language_and_version_come_from_the_path(
    tmp_path, monkeypatch, url, download
):
    seen = {}

    def fake_fetch_bytes(u, **kwargs):
        seen["url"] = u
        return u, _PDF

    monkeypatch.setattr(http, "fetch_bytes", fake_fetch_bytes)
    ReadTheDocsPattern().acquire(url, tmp_path)
    assert seen["url"] == download


def test_subproject_without_a_build_crawls_the_subproject(tmp_path, monkeypatch):
    def fake_fetch_bytes(url, **kwargs):
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]

    called = {}

    def fake_sphinx_acquire(base_url, workdir, *, slug, title):
        called["base"] = base_url
        called["slug"] = slug
        raw = workdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        return AcquireResult(raw_dir=raw, kind="html", slug=slug, pages=3, title=title)

    monkeypatch.setattr(http, "fetch_bytes", fake_fetch_bytes)
    monkeypatch.setattr(_sphinx, "acquire", fake_sphinx_acquire)
    ReadTheDocsPattern().acquire("https://parent.readthedocs.io/projects/child/en/v3/", tmp_path)
    assert called["base"] == "https://parent.readthedocs.io/projects/child/en/v3/"
    assert called["slug"] == "parent-child"


def test_acquire_downloads_pdf_build(tmp_path, monkeypatch):
    seen = {}

    def fake_fetch_bytes(url, **kwargs):
        seen["url"] = url
        return url, _PDF

    monkeypatch.setattr(http, "fetch_bytes", fake_fetch_bytes)
    p = ReadTheDocsPattern()
    acq = p.acquire("https://requests.readthedocs.io/en/stable/", tmp_path)
    assert seen["url"] == "https://requests.readthedocs.io/_/downloads/en/stable/pdf/"
    assert acq.kind == "pdf"
    assert acq.slug == "requests"
    assert acq.pages is None  # stub bytes carry no page tree to count
    out = p.normalize(acq, tmp_path)
    assert out.read_bytes() == _PDF


def test_a_pdf_build_gets_the_pdf_normalize(tmp_path, monkeypatch):
    """A Read the Docs build printed as spreads is cut like any other PDF deliverable."""
    from pagespring.patterns import _pdf_spreads

    def fake_single_pages(src, out):
        out.write_bytes(_PDF)
        return out, 2

    monkeypatch.setattr(http, "fetch_bytes", lambda url, **kw: (url, _PDF))
    monkeypatch.setattr(_pdf_spreads, "single_pages", fake_single_pages)
    p = ReadTheDocsPattern()
    acq = p.acquire("https://requests.readthedocs.io/en/stable/", tmp_path)

    out = p.normalize(acq, tmp_path)

    assert out.parent == tmp_path
    assert acq.spreads_split == 2


def test_acquire_missing_build_falls_back_to_sphinx_crawl(tmp_path, monkeypatch):
    def fake_fetch_bytes(url, **kwargs):
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]

    called = {}

    def fake_sphinx_acquire(base_url, workdir, *, slug, title):
        called["base"] = base_url
        called["slug"] = slug
        raw = workdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        return AcquireResult(raw_dir=raw, kind="html", slug=slug, pages=3, title=title)

    monkeypatch.setattr(http, "fetch_bytes", fake_fetch_bytes)
    monkeypatch.setattr(_sphinx, "acquire", fake_sphinx_acquire)
    p = ReadTheDocsPattern()
    acq = p.acquire("https://noproj.readthedocs.io/en/latest/", tmp_path)
    assert called["base"] == "https://noproj.readthedocs.io/en/latest/"
    assert called["slug"] == "noproj"
    assert acq.kind == "html"


def test_acquire_non_pdf_response_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_bytes", lambda url, **k: (url, b"<html>nope</html>"))
    with pytest.raises(InvalidInputError):
        ReadTheDocsPattern().acquire("https://x.readthedocs.io/", tmp_path)


def test_acquire_transient_http_error_propagates(tmp_path, monkeypatch):
    """Non-404 (e.g. exhausted 5xx retries) is a fetch failure, not 'builds disabled'."""

    def fake_fetch_bytes(url, **kwargs):
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr(http, "fetch_bytes", fake_fetch_bytes)
    with pytest.raises(urllib.error.HTTPError):
        ReadTheDocsPattern().acquire("https://x.readthedocs.io/en/latest/", tmp_path)
