"""pdf_url — match + mocked download (no network)."""

from pagespring import http
from pagespring.patterns.pdf_url import PdfUrlPattern


def test_match_pdf_extension():
    p = PdfUrlPattern()
    assert p.match("https://x.com/a/Manual.PDF")
    assert p.match("https://vendor.com/downloads/kemper.pdf")
    assert not p.match("https://x.com/page.html")
    assert not p.match("https://x.com/docs/")


def test_rtd_pdf_match_and_host_slug(tmp_path, monkeypatch):
    from pagespring.patterns.pdf_url import _slug_from_url

    p = PdfUrlPattern()
    # Read-the-Docs PDF builds live at an extensionless /pdf/ path.
    assert p.match("https://picard-docs.musicbrainz.org/_/downloads/en/latest/pdf/")
    assert (
        _slug_from_url("https://picard-docs.musicbrainz.org/_/downloads/en/latest/pdf/")
        == "picard-docs"
    )

    monkeypatch.setattr(http, "fetch_bytes", lambda u, **k: (u, b"%PDF-1.5 body"))
    acq = p.acquire("https://picard-docs.musicbrainz.org/_/downloads/en/latest/pdf/", tmp_path)
    assert acq.kind == "pdf"
    assert acq.slug == "picard-docs"
    assert next(acq.raw_dir.glob("*.pdf")).name == "picard-docs.pdf"


def test_acquire_downloads_and_slugs(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_bytes", lambda url, **kw: (url, b"%PDF-1.7 fake body"))
    p = PdfUrlPattern()
    acq = p.acquire("https://vendor.com/d/KEMPER_PROFILER_Main_14.0.pdf", tmp_path)

    assert acq.kind == "pdf"
    assert acq.slug == "kemper-profiler-main-14-0"
    assert acq.pages == 1
    pdfs = list(acq.raw_dir.glob("*.pdf"))
    assert len(pdfs) == 1
    assert pdfs[0].read_bytes().startswith(b"%PDF")

    clean = p.normalize(acq, tmp_path)
    assert clean.suffix == ".pdf"
    assert clean.read_bytes().startswith(b"%PDF")
