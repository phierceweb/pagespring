"""archive_download — match + download/extract/concat with a synthetic zip."""

import io
import zipfile

from pagespring import http
from pagespring.patterns.archive_download import ArchiveDownloadPattern


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("python-3.14-docs-text/intro.txt", "Intro text body.")
        z.writestr("python-3.14-docs-text/library/usage.txt", "Usage text body.")
    return buf.getvalue()


def test_match():
    p = ArchiveDownloadPattern()
    assert p.match("https://docs.python.org/3/archives/python-3.14-docs-text.zip")
    assert p.match("https://x.com/project.tar.bz2")
    assert p.match("https://x.com/book.epub")
    assert not p.match("https://x.com/manual.pdf")
    assert not p.match("https://x.com/page.html")


def test_acquire_captures_response_validators(tmp_path, monkeypatch):
    """The single-fetch archive download records ETag/Last-Modified so a
    refresh can probe with a conditional GET instead of re-downloading."""
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda url, **kw: (
            url,
            _zip_bytes(),
            {"etag": '"z9"', "last_modified": "Fri, 17 Jul 2026 09:00:00 GMT"},
        ),
    )
    acq = ArchiveDownloadPattern().acquire("https://x.com/docs.zip", tmp_path)
    assert acq.etag == '"z9"'
    assert acq.last_modified == "Fri, 17 Jul 2026 09:00:00 GMT"


def test_acquire_extracts_and_concats(tmp_path, monkeypatch):
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda url, **kw: (url, _zip_bytes(), {"etag": None, "last_modified": None}),
    )
    p = ArchiveDownloadPattern()

    acq = p.acquire("https://docs.python.org/3/archives/python-3.14-docs-text.zip", tmp_path)
    assert acq.kind == "markdown"
    assert acq.slug == "python-3-14-docs-text"
    assert acq.pages == 2  # the two extracted text files

    out = p.normalize(acq, tmp_path)
    assert out.name.endswith(".md")
    text = out.read_text(encoding="utf-8")
    assert "Intro text body." in text
    assert "Usage text body." in text
    # Sorted order: intro before library/usage.
    assert text.index("Intro text body.") < text.index("Usage text body.")


def _epub_bytes() -> bytes:
    """An EPUB whose spine order differs from lexical filename order — the
    Gutenberg shape, where ch10-12 sort between ch1 and ch2 and the cover last."""
    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <manifest>
    <item id="cover" href="wrap0000.html" media-type="application/xhtml+xml"/>
    <item id="c1" href="bk-1.htm.html" media-type="application/xhtml+xml"/>
    <item id="c2" href="bk-2.htm.html" media-type="application/xhtml+xml"/>
    <item id="c10" href="bk-10.htm.html" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="cover"/><itemref idref="c1"/><itemref idref="c2"/><itemref idref="c10"/></spine>
</package>"""
    page = (
        '<?xml version="1.0"?><!DOCTYPE html><html><head><title>Book</title>'
        "<style>p{{margin:0}}</style></head><body><h1>{h}</h1><p>{b}</p></body></html>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/wrap0000.html", page.format(h="Cover", b="Cover art."))
        z.writestr("OEBPS/bk-1.htm.html", page.format(h="Chapter I", b="First chapter."))
        z.writestr("OEBPS/bk-2.htm.html", page.format(h="Chapter II", b="Second chapter."))
        z.writestr("OEBPS/bk-10.htm.html", page.format(h="Chapter X", b="Tenth chapter."))
    return buf.getvalue()


def test_epub_members_follow_the_spine_not_the_filename(tmp_path, monkeypatch):
    """Lexical sort put Alice's chapters in the order I, X, XI, XII, II, III …
    and the cover last. The OPF spine is the book's real reading order."""
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda url, **kw: (url, _epub_bytes(), {"etag": None, "last_modified": None}),
    )
    p = ArchiveDownloadPattern()
    acq = p.acquire("https://www.gutenberg.org/cache/epub/11/pg11.epub", tmp_path)
    out = p.normalize(acq, tmp_path).read_text(encoding="utf-8")

    order = [
        out.index(x) for x in ("Cover art.", "First chapter.", "Second chapter.", "Tenth chapter.")
    ]
    assert order == sorted(order), f"members out of reading order: {order}"


def test_html_members_contribute_body_not_whole_documents(tmp_path, monkeypatch):
    """Concatenating whole XHTML files nested 14 DOCTYPE/<html>/<head> blocks
    inside one deliverable — invalid, and it buried 14 duplicate <title>s."""
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda url, **kw: (url, _epub_bytes(), {"etag": None, "last_modified": None}),
    )
    p = ArchiveDownloadPattern()
    acq = p.acquire("https://www.gutenberg.org/cache/epub/11/pg11.epub", tmp_path)
    out = p.normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert "First chapter." in out and "Chapter I" in out
    assert (
        "<!DOCTYPE" not in out.upper().replace("<!DOCTYPE HTML>", "", 1)
        or out.upper().count("<!DOCTYPE") <= 1
    )
    assert out.count("<html") <= 1, "nested <html> documents"
    assert out.count("<title>") <= 1, "duplicate per-chapter <title> elements"
    assert "<style" not in out, "per-chapter inline CSS survived"
