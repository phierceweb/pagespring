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

    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda u, **k: (u, b"%PDF-1.5 body", {"etag": None, "last_modified": None}),
    )
    acq = p.acquire("https://picard-docs.musicbrainz.org/_/downloads/en/latest/pdf/", tmp_path)
    assert acq.kind == "pdf"
    assert acq.slug == "picard-docs"
    assert next(acq.raw_dir.glob("*.pdf")).name == "picard-docs.pdf"


def test_acquire_captures_response_validators(tmp_path, monkeypatch):
    """The single-fetch download records ETag/Last-Modified so a refresh can
    probe with a conditional GET instead of re-downloading the PDF."""
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda url, **kw: (
            url,
            b"%PDF-1.7 body",
            {"etag": '"v42"', "last_modified": "Sat, 18 Jul 2026 10:00:00 GMT"},
        ),
    )
    acq = PdfUrlPattern().acquire("https://vendor.com/d/manual.pdf", tmp_path)
    assert acq.etag == '"v42"'
    assert acq.last_modified == "Sat, 18 Jul 2026 10:00:00 GMT"


def test_acquire_downloads_and_slugs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda url, **kw: (url, b"%PDF-1.7 fake body", {"etag": None, "last_modified": None}),
    )
    p = PdfUrlPattern()
    acq = p.acquire("https://vendor.com/d/KEMPER_PROFILER_Main_14.0.pdf", tmp_path)

    assert acq.kind == "pdf"
    assert acq.slug == "kemper-profiler-main-14-0"
    assert acq.pages is None  # magic bytes only — no page tree to count
    pdfs = list(acq.raw_dir.glob("*.pdf"))
    assert len(pdfs) == 1
    assert pdfs[0].read_bytes().startswith(b"%PDF")

    clean = p.normalize(acq, tmp_path)
    assert clean.suffix == ".pdf"
    assert clean.read_bytes().startswith(b"%PDF")


def test_acquire_rejects_a_response_that_is_not_a_pdf(tmp_path, monkeypatch):
    """helpx.adobe.com/pdf/<x>_reference.pdf 301s to an HTML landing page and
    returns 200 text/html. Without a magic-byte check the HTML is staged as
    <slug>.pdf and the deliverable is a lie no later check can catch — audit
    never content-checks a kind:pdf deliverable."""
    import pytest
    from pf_core.exceptions import InvalidInputError

    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda u, **k: (
            u,
            b"<!DOCTYPE html>\n<html><body>Desktop Help</body></html>",
            {"etag": None, "last_modified": None},
        ),
    )
    with pytest.raises(InvalidInputError, match="not a PDF"):
        PdfUrlPattern().acquire("https://helpx.adobe.com/pdf/illustrator_reference.pdf", tmp_path)


def test_acquire_accepts_a_pdf_with_leading_whitespace(tmp_path, monkeypatch):
    """Some servers prepend whitespace/BOM before %PDF — still a real PDF."""
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda u, **k: (u, b"\r\n%PDF-1.7 body", {"etag": None, "last_modified": None}),
    )
    acq = PdfUrlPattern().acquire("https://vendor.com/m.pdf", tmp_path)
    assert acq.kind == "pdf"


def _pdf_bytes(pages: int) -> bytes:
    """A minimal valid multi-page PDF, hand-built so the fixture does not depend
    on the same library the code under test uses."""
    objs = [
        "<</Type/Catalog/Pages 2 0 R>>",
        "<</Type/Pages/Kids[{}]/Count {}>>".format(
            " ".join(f"{3 + i} 0 R" for i in range(pages)), pages
        ),
    ]
    objs += ["<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>"] * pages
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj {obj} endobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer <</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def test_pages_records_the_pdf_page_count_not_the_file_count(tmp_path, monkeypatch):
    """`pages: 1` for every PDF was true-but-useless — it counted files fetched.
    For a PDF the source unit is the page, and downstream reads it that way."""
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda url, **kw: (url, _pdf_bytes(37), {"etag": None, "last_modified": None}),
    )
    acq = PdfUrlPattern().acquire("https://vendor.example/manual.pdf", tmp_path)
    assert acq.pages == 37


def test_unreadable_pdf_records_no_page_count(tmp_path, monkeypatch):
    """A damaged PDF must not fail the ingest — the deliverable is still the
    file. pages=None says 'unknown', which is honest; 1 is a lie."""
    broken = b"%PDF-1.7\n" + b"garbage that is not a page tree\n" * 20
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda url, **kw: (url, broken, {"etag": None, "last_modified": None}),
    )
    acq = PdfUrlPattern().acquire("https://vendor.example/manual.pdf", tmp_path)
    assert acq.pages is None
