"""archive_download — match + download/extract/concat with a synthetic zip."""

import io
import os
import tarfile
import zipfile

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.patterns import _archive_extract
from pagespring.patterns.archive_download import ArchiveDownloadPattern


@pytest.fixture(autouse=True)
def _default_extraction_budget(monkeypatch):
    monkeypatch.delenv("PAGESPRING_MAX_EXTRACT_BYTES", raising=False)


def _serve(monkeypatch, data: bytes, etag=None, last_modified=None) -> None:
    meta = {"etag": etag, "last_modified": last_modified}
    monkeypatch.setattr(http, "fetch_bytes_meta", lambda url, **kw: (url, data, meta))


def _deflated_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def _tgz(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


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
    _serve(monkeypatch, _zip_bytes(), etag='"z9"', last_modified="Fri, 17 Jul 2026 09:00:00 GMT")
    acq = ArchiveDownloadPattern().acquire("https://x.com/docs.zip", tmp_path)
    assert acq.etag == '"z9"'
    assert acq.last_modified == "Fri, 17 Jul 2026 09:00:00 GMT"


def test_acquire_extracts_and_concats(tmp_path, monkeypatch):
    _serve(monkeypatch, _zip_bytes())
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
    """Lexical sort puts chapter 10 between 1 and 2 and the cover last; the OPF
    spine is the book's real reading order."""
    _serve(monkeypatch, _epub_bytes())
    p = ArchiveDownloadPattern()
    acq = p.acquire("https://www.gutenberg.org/cache/epub/11/pg11.epub", tmp_path)
    out = p.normalize(acq, tmp_path).read_text(encoding="utf-8")

    order = [
        out.index(x) for x in ("Cover art.", "First chapter.", "Second chapter.", "Tenth chapter.")
    ]
    assert order == sorted(order), f"members out of reading order: {order}"


def test_html_members_contribute_body_not_whole_documents(tmp_path, monkeypatch):
    """Concatenating whole XHTML files nests a DOCTYPE/<html>/<head> block and a
    <title> per chapter inside one deliverable — invalid markup."""
    _serve(monkeypatch, _epub_bytes())
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
    """EPUB 3 names content documents .xhtml. A sniff that knows only .html/.htm
    classifies the book as markdown, filters every chapter out, and stages the
    stray COPYRIGHT.txt as the entire deliverable with a healthy-looking manifest."""
    _serve(monkeypatch, _epub3_bytes())
    p = ArchiveDownloadPattern()
    acq = p.acquire("https://standardebooks.org/x/y/downloads/book.epub", tmp_path)

    assert acq.kind == "html", "an EPUB of .xhtml chapters is an HTML archive"
    assert acq.pages == 2, f"both chapters must count as pages, got {acq.pages}"

    out = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "First chapter." in out and "Second chapter." in out
    assert "public domain" not in out, "stray .txt leaked into an HTML deliverable"
    assert out.index("First chapter.") < out.index("Second chapter."), "spine order lost"


def _gutenberg_shaped_epub(nav_in_spine: bool = False) -> bytes:
    """The Project Gutenberg EPUB 3 shape: a dc:title, a nav document, and the book
    wrapped in a pg-boilerplate header and a license-footer member."""
    nav_ref = '<itemref idref="nav"/>' if nav_in_spine else ""
    opf = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/"
         version="3.0">
  <metadata><dc:title> The Tale of a Test </dc:title></metadata>
  <manifest>
    <item id="nav" href="toc.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="pg-header" href="book-h-0.htm.xhtml" media-type="application/xhtml+xml"/>
    <item id="pg-footer" href="book-h-1.htm.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>{nav_ref}<itemref idref="pg-header"/><itemref idref="pg-footer"/></spine>
</package>"""
    page = '<html xmlns="http://www.w3.org/1999/xhtml"><body>{}</body></html>'
    header = (
        '<header class="pg-boilerplate pgheader" id="pg-header">'
        "<h2>The Project Gutenberg eBook of The Tale</h2><div>GUTENBERG TERMS OF USE</div></header>"
    )
    book = '<h1 id="c1">Chapter One</h1><p>The story body.</p>'
    footer = '<footer class="pg-boilerplate pgheader"><div>FULL GUTENBERG LICENSE</div></footer>'
    nav = '<nav><ol><li><a href="book-h-0.htm.xhtml#c1">TOC ENTRY</a></li></ol></nav>'
    return _deflated_zip(
        {
            "mimetype": b"application/epub+zip",
            "OEBPS/content.opf": opf.encode(),
            "OEBPS/toc.xhtml": page.format(nav).encode(),
            "OEBPS/book-h-0.htm.xhtml": page.format(header + book).encode(),
            "OEBPS/book-h-1.htm.xhtml": page.format(footer).encode(),
        }
    )


def test_the_opf_title_names_the_deliverable(tmp_path, monkeypatch):
    """Without it the heading falls back to the slug the download URL yields."""
    _serve(monkeypatch, _gutenberg_shaped_epub())
    p = ArchiveDownloadPattern()
    acq = p.acquire("https://www.gutenberg.org/cache/epub/1/pg1-images-3.epub", tmp_path)

    assert acq.title == "The Tale of a Test"
    assert "<title>The Tale of a Test</title>" in p.normalize(acq, tmp_path).read_text("utf-8")


def test_a_navigation_document_the_spine_omits_is_not_appended(tmp_path, monkeypatch):
    """Every EPUB 3 carries one: the reading system's table of contents, whose links
    name member files the deliverable does not have."""
    _serve(monkeypatch, _gutenberg_shaped_epub())
    p = ArchiveDownloadPattern()
    acq = p.acquire("https://x.com/book.epub", tmp_path)
    out = p.normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert "TOC ENTRY" not in out
    assert acq.pages == 1, f"the nav document counted as a page: {acq.pages}"


def test_a_navigation_document_in_the_spine_keeps_its_place(tmp_path, monkeypatch):
    _serve(monkeypatch, _gutenberg_shaped_epub(nav_in_spine=True))
    p = ArchiveDownloadPattern()
    out = p.normalize(p.acquire("https://x.com/book.epub", tmp_path), tmp_path).read_text("utf-8")

    assert out.index("TOC ENTRY") < out.index("The story body.")


def test_gutenberg_boilerplate_is_not_part_of_the_book(tmp_path, monkeypatch):
    """Project Gutenberg marks its header and license footer pg-boilerplate; the
    license alone can outweigh a short book."""
    _serve(monkeypatch, _gutenberg_shaped_epub())
    p = ArchiveDownloadPattern()
    out = p.normalize(p.acquire("https://x.com/book.epub", tmp_path), tmp_path).read_text("utf-8")

    assert "Chapter One" in out and "The story body." in out
    assert "TERMS OF USE" not in out
    assert "FULL GUTENBERG LICENSE" not in out
    assert "book-h-1.htm.xhtml" not in out, "the member left empty was still emitted"


def _spine_epub(bodies: list[str]) -> bytes:
    items = "".join(
        f'<item id="m{i}" href="m{i}.xhtml" media-type="application/xhtml+xml"/>'
        for i in range(len(bodies))
    )
    refs = "".join(f'<itemref idref="m{i}"/>' for i in range(len(bodies)))
    opf = (
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        f"<manifest>{items}</manifest><spine>{refs}</spine></package>"
    )
    members = {"mimetype": b"application/epub+zip", "OEBPS/content.opf": opf.encode()}
    for i, body in enumerate(bodies):
        members[f"OEBPS/m{i}.xhtml"] = f"<html><body>{body}</body></html>".encode()
    return _deflated_zip(members)


def test_pages_counts_only_the_members_the_deliverable_holds(tmp_path, monkeypatch):
    """A member emptied by boilerplate removal, even inside a wrapper, is not a page;
    a member that is only a figure is."""
    _serve(
        monkeypatch,
        _spine_epub(
            [
                '<div class="wrap"><footer class="pg-boilerplate">FULL LICENSE</footer></div>',
                '<div><img src="plate.png" alt=""/></div>',
                "<p>The story.</p>",
                "<script>track()</script>",
            ]
        ),
    )
    p = ArchiveDownloadPattern()
    acq = p.acquire("https://x.com/book.epub", tmp_path)
    out = p.normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert acq.pages == out.count("<!-- source:") == 2
    assert "m1.xhtml" in out and "m2.xhtml" in out
    assert "FULL LICENSE" not in out and "m0.xhtml" not in out


def test_text_and_comments_directly_in_a_body_keep_their_markup(tmp_path, monkeypatch):
    _serve(monkeypatch, _spine_epub(["<!-- colophon --> 3 &lt; 4 <p>x &amp; y</p>"]))
    p = ArchiveDownloadPattern()
    out = p.normalize(p.acquire("https://x.com/book.epub", tmp_path), tmp_path).read_text("utf-8")

    assert "<!-- colophon --> 3 &lt; 4 <p>x &amp; y</p>" in out


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
    """.xhtml is an EPUB content-document convention. Counted as an HTML member
    everywhere, it flips this archive's kind to html, filters both .md docs out,
    and stages the boilerplate as the whole deliverable — silently, since
    `single_fetch` suppresses audit's single_page_crawl on the 1-page result."""
    _serve(monkeypatch, _mixed_zip_bytes())
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
    """A case-sensitive sniff classifies a zip of .HTML pages as markdown, filters
    every page out, and stages the packaging README as the entire deliverable —
    with a healthy-looking manifest. pathlib globs case-sensitively even on APFS."""
    _serve(monkeypatch, _uppercase_html_zip_bytes())
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
    _serve(monkeypatch, _text_zip_with_stray_html())
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
    _serve(monkeypatch, _html_zip_with_packaging_readme())
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
    _serve(monkeypatch, _text_zip_with_one_stub())
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
    _serve(monkeypatch, _epub_with_same_named_chapters())
    p = ArchiveDownloadPattern()
    acq = p.acquire("https://x.com/book.epub", tmp_path)
    out = p.normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert out.count("PART ONE BODY") == 1, "part one duplicated"
    assert out.count("PART TWO BODY") == 1, "part two dropped or duplicated"
    assert out.index("PART ONE BODY") < out.index("PART TWO BODY"), "spine order lost"


def _epub_with_escaped_hrefs() -> bytes:
    """An EPUB whose manifest hrefs percent-encode spaces and carry a fragment,
    both legal URL spellings of the extracted member names."""
    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="cv" href="Text/Cover.xhtml" media-type="application/xhtml+xml"/>
    <item id="c1" href="Text/Chapter%201.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="Text/Chapter%202.xhtml#start" media-type="application/xhtml+xml"/>
    <item id="ap" href="Text/Appendix%20A.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="cv"/><itemref idref="c1"/><itemref idref="c2"/><itemref idref="ap"/></spine>
</package>"""
    return _deflated_zip(
        {"OEBPS/content.opf": opf.encode()}
        | {
            f"OEBPS/Text/{name}.xhtml": f"<html><body><p>{name} BODY</p></body></html>".encode()
            for name in ("Cover", "Chapter 1", "Chapter 2", "Appendix A")
        }
    )


def test_percent_encoded_spine_hrefs_keep_reading_order(tmp_path, monkeypatch):
    """`Chapter%201.xhtml` names the extracted `Chapter 1.xhtml`; compared raw, no
    escaped chapter matches and the appendix sorts ahead of chapter one."""
    _serve(monkeypatch, _epub_with_escaped_hrefs())
    p = ArchiveDownloadPattern()
    acq = p.acquire("https://x.com/book.epub", tmp_path)
    out = p.normalize(acq, tmp_path).read_text(encoding="utf-8")

    bodies = ("Cover BODY", "Chapter 1 BODY", "Chapter 2 BODY", "Appendix A BODY")
    assert all(out.count(b) == 1 for b in bodies)
    order = [out.index(b) for b in bodies]
    assert order == sorted(order), f"members out of spine order: {order}"


@pytest.mark.parametrize("pack", [_deflated_zip, _tgz], ids=["zip", "tar.gz"])
def test_an_implausible_compression_ratio_is_refused_before_extraction(tmp_path, monkeypatch, pack):
    """About 40 KB of deflated zeros inflates to 42 MB, so the download cap alone
    lets one archive expand a thousandfold onto disk."""
    _serve(monkeypatch, pack({"docs/page.txt": bytes(42 * 1024 * 1024)}))
    with pytest.raises(InvalidInputError, match="compression ratio"):
        ArchiveDownloadPattern().acquire("https://x.com/docs.zip", tmp_path)
    assert not any((tmp_path / "raw").rglob("*")), "members extracted before the refusal"


def _two_incompressible_members(pack) -> bytes:
    return pack({"a.txt": os.urandom(100_000), "b.txt": os.urandom(100_000)})


@pytest.mark.parametrize("pack", [_deflated_zip, _tgz], ids=["zip", "tar.gz"])
def test_an_archive_past_the_member_cap_is_refused(tmp_path, monkeypatch, pack):
    monkeypatch.setattr(_archive_extract, "_MAX_MEMBERS", 1)
    _serve(monkeypatch, _two_incompressible_members(pack))
    with pytest.raises(InvalidInputError, match="members"):
        ArchiveDownloadPattern().acquire("https://x.com/docs.zip", tmp_path)
    assert not any((tmp_path / "raw").rglob("*"))


@pytest.mark.parametrize("pack", [_deflated_zip, _tgz], ids=["zip", "tar.gz"])
def test_an_archive_past_the_extraction_budget_is_refused(tmp_path, monkeypatch, pack):
    monkeypatch.setenv("PAGESPRING_MAX_EXTRACT_BYTES", "150000")
    _serve(monkeypatch, _two_incompressible_members(pack))
    with pytest.raises(InvalidInputError, match="extract past 150000 bytes"):
        ArchiveDownloadPattern().acquire("https://x.com/docs.zip", tmp_path)
    assert not any((tmp_path / "raw").rglob("*"))


def test_the_extraction_budget_can_be_raised_past_its_default(tmp_path, monkeypatch):
    """A legitimate archive larger than the default budget extracts once the
    operator raises it, as the download cap already can be."""
    monkeypatch.setattr(_archive_extract, "_MAX_EXTRACT_BYTES_DEFAULT", 150_000)
    monkeypatch.setenv("PAGESPRING_MAX_EXTRACT_BYTES", "1000000")
    _serve(monkeypatch, _two_incompressible_members(_deflated_zip))
    assert ArchiveDownloadPattern().acquire("https://x.com/docs.zip", tmp_path).pages == 2


@pytest.mark.parametrize("value", ["not-a-number", "", "0", "-1"])
def test_an_unusable_extraction_budget_falls_back_to_the_default(monkeypatch, value):
    """A malformed or non-positive override must not lift the budget."""
    monkeypatch.setenv("PAGESPRING_MAX_EXTRACT_BYTES", value)
    assert _archive_extract._max_extract_bytes() == _archive_extract._MAX_EXTRACT_BYTES_DEFAULT


@pytest.mark.parametrize("pack", [_deflated_zip, _tgz], ids=["zip", "tar.gz"])
def test_a_small_highly_compressible_archive_still_extracts(tmp_path, monkeypatch, pack):
    """Repetitive text compresses far past the ratio cap; below the floor the ratio
    is no evidence of a bomb."""
    _serve(monkeypatch, pack({"docs/page.txt": b"Lorem ipsum. " * 200_000}))
    assert ArchiveDownloadPattern().acquire("https://x.com/docs.zip", tmp_path).pages == 1


def _corrupt_deflate_zip() -> bytes:
    data = bytearray(_deflated_zip({"docs/page.txt": b"page body " * 500}))
    info = zipfile.ZipFile(io.BytesIO(bytes(data))).infolist()[0]
    start = info.header_offset + 30 + len(info.filename)
    data[start : start + info.compress_size] = b"\xff" * info.compress_size
    return bytes(data)


@pytest.mark.parametrize(
    ("data", "why"),
    [
        pytest.param(b"<!DOCTYPE html><html><body>Sign in</body></html>", "HTML page", id="html"),
        pytest.param(_zip_bytes().replace(b"Intro", b"INTRO"), "damaged", id="bad-crc"),
        pytest.param(_corrupt_deflate_zip(), "damaged", id="corrupt-deflate"),
        pytest.param(_tgz({"page.txt": b"x" * 5000})[:-40], "damaged", id="truncated-tgz"),
        pytest.param(_tgz({"../evil.txt": b"escape"}), "outside", id="tar-member-escapes"),
    ],
)
def test_an_unreadable_or_unsafe_archive_is_invalid_input(tmp_path, monkeypatch, data, why):
    """A login page at a .zip URL, a damaged download or a member escaping the
    extraction root is a bad source (exit 2), not a traceback."""
    _serve(monkeypatch, data)
    with pytest.raises(InvalidInputError, match=f"docs.zip: .*{why}"):
        ArchiveDownloadPattern().acquire("https://x.com/docs.zip", tmp_path)


def test_a_corrupt_local_archive_names_the_file(tmp_path):
    archive = tmp_path / "broken-manual.zip"
    archive.write_bytes(b"PK\x03\x04" + bytes(40))
    with pytest.raises(InvalidInputError, match="broken-manual.zip: not a zip"):
        ArchiveDownloadPattern().acquire(str(archive), tmp_path / "w")


def test_an_html_archive_parses_each_member_once(tmp_path, monkeypatch):
    from pagespring.patterns import archive_download

    parsed: list[str] = []
    content = archive_download._content
    monkeypatch.setattr(
        archive_download, "_content", lambda member: parsed.append(member.name) or content(member)
    )
    _serve(monkeypatch, _spine_epub(["<p>One.</p>", "<script>track()</script>", "<p>Two.</p>"]))
    p = ArchiveDownloadPattern()
    acq = p.acquire("https://x.com/book.epub", tmp_path)
    p.normalize(acq, tmp_path)

    assert sorted(parsed) == ["m0.xhtml", "m1.xhtml", "m2.xhtml"]
    assert acq.pages == 2
