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


def test_acquire_extracts_and_concats(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_bytes", lambda url, **kw: (url, _zip_bytes()))
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
