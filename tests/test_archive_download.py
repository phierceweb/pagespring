"""archive_download — match + download/extract/concat with a synthetic zip."""

import io
import zipfile

import pytest
from pf_core.exceptions import InvalidInputError

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


def _epub3_bytes() -> bytes:
    """EPUB 3 with the spec-conventional .xhtml content documents, plus the
    stray COPYRIGHT.txt that packaged EPUBs routinely carry."""
    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="c1" href="chapter-1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="chapter-2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""
    page = (
        '<?xml version="1.0"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml">'
        "<head><title>Book</title></head><body><h1>{h}</h1><p>{b}</p></body></html>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("epub/content.opf", opf)
        z.writestr("epub/chapter-1.xhtml", page.format(h="Chapter One", b="First chapter."))
        z.writestr("epub/chapter-2.xhtml", page.format(h="Chapter Two", b="Second chapter."))
        z.writestr("epub/COPYRIGHT.txt", "This book is in the public domain.")
    return buf.getvalue()


def test_epub3_xhtml_chapters_are_the_deliverable(tmp_path, monkeypatch):
    """EPUB 3 names content documents .xhtml. Sniffing only .html/.htm classified
    the book as markdown, filtered every chapter out, and staged the stray
    COPYRIGHT.txt as the entire deliverable with a healthy-looking manifest."""
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda url, **kw: (url, _epub3_bytes(), {"etag": None, "last_modified": None}),
    )
    p = ArchiveDownloadPattern()
    acq = p.acquire("https://standardebooks.org/x/y/downloads/book.epub", tmp_path)

    assert acq.kind == "html", "an EPUB of .xhtml chapters is an HTML archive"
    assert acq.pages == 2, f"both chapters must count as pages, got {acq.pages}"

    out = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "First chapter." in out and "Second chapter." in out
    assert "public domain" not in out, "stray .txt leaked into an HTML deliverable"
    assert out.index("First chapter.") < out.index("Second chapter."), "spine order lost"


def _mixed_zip_bytes() -> bytes:
    """A markdown docs zip carrying one stray .xhtml, the shape a packaged
    legal notice or vendored fragment produces."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("docs/intro.md", "# Intro\n\nIntro body.")
        z.writestr("docs/guide.md", "# Guide\n\nGuide body.")
        z.writestr("docs/legal/notice.xhtml", "<html><body><p>boilerplate</p></body></html>")
    return buf.getvalue()


def test_stray_xhtml_does_not_flip_a_markdown_archive_to_html(tmp_path, monkeypatch):
    """.xhtml is an EPUB content-document convention. Counting it as an HTML
    member everywhere flipped this archive's kind to html, filtered both .md docs
    out, and staged the boilerplate as the whole deliverable — silently, since
    `single_fetch` suppresses audit's single_page_crawl on the 1-page result."""
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda url, **kw: (url, _mixed_zip_bytes(), {"etag": None, "last_modified": None}),
    )
    p = ArchiveDownloadPattern()
    acq = p.acquire("https://x.com/project-docs.zip", tmp_path)

    assert acq.kind == "markdown", "a stray .xhtml is not an HTML archive"
    assert acq.pages == 2, f"both markdown docs must count as pages, got {acq.pages}"

    out = p.normalize(acq, tmp_path)
    assert out.name.endswith(".md")
    text = out.read_text(encoding="utf-8")
    assert "Intro body." in text and "Guide body." in text
    assert "boilerplate" not in text, "the stray .xhtml replaced the documentation"


def _uppercase_html_zip_bytes() -> bytes:
    """An HTML docs zip whose members carry uppercase extensions."""
    page = "<HTML><BODY><H1>{h}</H1><P>{b}</P></BODY></HTML>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("docs/CHAPTER1.HTML", page.format(h="One", b="First chapter."))
        z.writestr("docs/CHAPTER2.HTML", page.format(h="Two", b="Second chapter."))
        z.writestr("docs/README.txt", "Unpack and open CHAPTER1.HTML.")
    return buf.getvalue()


def test_uppercase_html_members_are_sniffed_as_html(tmp_path, monkeypatch):
    """A case-sensitive sniff classified a zip of .HTML pages as markdown, filtered
    every page out, and staged the packaging README as the entire deliverable —
    with a healthy-looking manifest. pathlib globs case-sensitively even on APFS."""
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda url, **kw: (url, _uppercase_html_zip_bytes(), {"etag": None, "last_modified": None}),
    )
    p = ArchiveDownloadPattern()
    acq = p.acquire("https://x.com/manual.zip", tmp_path)

    assert acq.kind == "html", "uppercase .HTML members are still an HTML archive"
    assert acq.pages == 2, f"both pages must count, got {acq.pages}"

    out = p.normalize(acq, tmp_path)
    assert out.name.endswith(".html")
    text = out.read_text(encoding="utf-8")
    assert "First chapter." in text and "Second chapter." in text
    assert "Unpack and open" not in text, "packaging README leaked into an HTML deliverable"


def _text_zip_with_stray_html() -> bytes:
    """A Python-docs-style text archive carrying one stray .html — the shape a
    packaged search page or redirect stub produces."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(1, 4):
            z.writestr(f"docs/chapter{i}.txt", f"Chapter {i} body.")
        z.writestr("docs/search.html", "<html><body>search form</body></html>")
    return buf.getvalue()


def test_one_stray_html_does_not_flip_a_text_archive_to_html(tmp_path, monkeypatch):
    """One .html member must not reclassify a text archive: the html filter then drops
    every .txt and stages the stub as the whole deliverable — the .xhtml case above,
    through the plain .html branch."""
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda url, **kw: (url, _text_zip_with_stray_html(), {"etag": None, "last_modified": None}),
    )
    p = ArchiveDownloadPattern()
    acq = p.acquire("https://x.com/python-3.14-docs-text.zip", tmp_path)

    assert acq.kind == "markdown", "three .txt docs outweigh one stray .html"
    assert acq.pages == 3, f"every text doc must count as a page, got {acq.pages}"

    text = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    for i in range(1, 4):
        assert f"Chapter {i} body." in text, f"chapter {i} dropped from the deliverable"
    assert "search form" not in text, "stray .html leaked into a text deliverable"


def _html_zip_with_packaging_readme() -> bytes:
    """A single-page HTML manual shipped beside its packaging README — one member
    of each family, the count the sniff has to break."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "manual.html", "<html><body><h1>Manual</h1><p>The real manual body.</p></body></html>"
        )
        z.writestr("README.md", "# Building this archive\n\nUnpack and open manual.html.\n")
    return buf.getvalue()


def test_an_equal_member_count_stages_the_html_manual_not_the_readme(tmp_path, monkeypatch):
    """A tie is an HTML archive: the text family here is the packaging README, and
    resolving to markdown filters the manual out and stages the README as the whole
    deliverable — with a valid manifest and a clean audit, so nothing else catches it."""
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda url, **kw: (
            url,
            _html_zip_with_packaging_readme(),
            {"etag": None, "last_modified": None},
        ),
    )
    p = ArchiveDownloadPattern()
    acq = p.acquire("https://x.com/manual.zip", tmp_path)

    assert acq.kind == "html", "one .html against one .md is an HTML archive"
    assert acq.pages == 1

    text = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "The real manual body." in text, "the manual was dropped from its own deliverable"
    assert "Unpack and open" not in text, "packaging README staged as the manual"


def _text_zip_with_one_stub() -> bytes:
    """A one-chapter text manual beside a search stub — the mirror of the packaging
    README case, with the junk in the other family."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("docs/chapter1.txt", "Chapter 1 body.")
        z.writestr("docs/search.html", "<html><body>search form</body></html>")
    return buf.getvalue()


def test_a_lone_stub_does_not_outvote_a_lone_text_chapter(tmp_path, monkeypatch):
    """The mirror of the case above: breaking the tie on raw counts alone swings this
    one the wrong way, staging the stub and dropping the only chapter. Which family
    holds the junk is the signal, not how many members each has."""
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda url, **kw: (url, _text_zip_with_one_stub(), {"etag": None, "last_modified": None}),
    )
    p = ArchiveDownloadPattern()
    acq = p.acquire("https://x.com/docs.zip", tmp_path)

    assert acq.kind == "markdown", "one chapter against one stub is still a text archive"

    text = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "Chapter 1 body." in text, "the only chapter was dropped from its own deliverable"
    assert "search form" not in text, "the search stub was staged as the manual"


def test_a_local_archive_is_read_from_disk(tmp_path):
    """`classify ./manual.zip` answers archive_download, so ingest has to honour it.
    Handing the bare path to the fetcher fails on scheme — an error naming neither
    the file nor anything the caller can act on."""
    archive = tmp_path / "widget-manual.zip"
    archive.write_bytes(_html_zip_with_packaging_readme())
    work = tmp_path / "work"

    p = ArchiveDownloadPattern()
    acq = p.acquire(str(archive), work)

    assert acq.slug == "widget-manual"
    assert acq.kind == "html"
    text = p.normalize(acq, work).read_text(encoding="utf-8")
    assert "The real manual body." in text


def test_a_file_url_archive_is_read_from_disk(tmp_path):
    """The `file://` spelling of the same source, which `_staging` already treats as
    one manual with the bare path."""
    archive = tmp_path / "widget-manual.zip"
    archive.write_bytes(_html_zip_with_packaging_readme())
    work = tmp_path / "work"

    acq = ArchiveDownloadPattern().acquire(archive.as_uri(), work)

    assert acq.slug == "widget-manual"
    assert acq.kind == "html"


def test_a_missing_local_archive_names_the_file(tmp_path):
    """A typo'd path must say what it could not find, not surface a scheme error."""
    with pytest.raises(InvalidInputError, match="no-such-manual"):
        ArchiveDownloadPattern().acquire(str(tmp_path / "no-such-manual.zip"), tmp_path / "w")


def _epub_with_same_named_chapters() -> bytes:
    """An EPUB whose spine lists two chapters that share a basename in different
    folders — the shape a per-part directory layout produces."""
    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="c1" href="part1/chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="part2/chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""
    page = "<html><body><h1>{h}</h1><p>{b}</p></body></html>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/part1/chapter.xhtml", page.format(h="One", b="PART ONE BODY"))
        z.writestr("OEBPS/part2/chapter.xhtml", page.format(h="Two", b="PART TWO BODY"))
    return buf.getvalue()


def test_spine_members_sharing_a_basename_are_not_confused(tmp_path, monkeypatch):
    """Two chapters can share a basename in different folders. Matching the spine on
    basename alone collapses them onto one Path: one is emitted twice and the other
    pushed out of reading order to the end."""
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda url, **kw: (
            url,
            _epub_with_same_named_chapters(),
            {"etag": None, "last_modified": None},
        ),
    )
    p = ArchiveDownloadPattern()
    acq = p.acquire("https://x.com/book.epub", tmp_path)
    out = p.normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert out.count("PART ONE BODY") == 1, "part one duplicated"
    assert out.count("PART TWO BODY") == 1, "part two dropped or duplicated"
    assert out.index("PART ONE BODY") < out.index("PART TWO BODY"), "spine order lost"
