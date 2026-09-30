"""Links between an HTML archive's members point inside the one deliverable."""

import io
import zipfile

import pytest
from bs4 import BeautifulSoup

from pagespring import http
from pagespring.patterns.archive_download import ArchiveDownloadPattern

_OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>{items}</manifest>
  <spine>{refs}</spine>
</package>"""


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _page(body: str) -> str:
    return f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'


def _epub(chapters: dict[str, str], nav: str | None = None) -> bytes:
    """An EPUB under ``OEBPS/`` whose spine lists ``chapters`` (href -> body) in order,
    with ``nav`` as the body of its navigation document ``toc.xhtml``, off the spine."""
    items = "".join(
        f'<item id="c{i}" href="{href}" media-type="application/xhtml+xml"/>'
        for i, href in enumerate(chapters)
    )
    if nav is not None:
        items += '<item id="nav" href="toc.xhtml" properties="nav"/>'
    refs = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
    files = {
        "mimetype": "application/epub+zip",
        "OEBPS/content.opf": _OPF.format(items=items, refs=refs),
    }
    for href, body in {**chapters, **({"toc.xhtml": nav} if nav is not None else {})}.items():
        files[f"OEBPS/{href}"] = _page(body)
    return _zip(files)


def _normalize(tmp_path, monkeypatch, data: bytes, url: str = "https://x.com/book.epub") -> str:
    meta = {"etag": None, "last_modified": None}
    monkeypatch.setattr(http, "fetch_bytes_meta", lambda u, **kw: (u, data, meta))
    p = ArchiveDownloadPattern()
    return p.normalize(p.acquire(url, tmp_path), tmp_path).read_text(encoding="utf-8")


def test_a_link_to_an_anchor_in_another_member_becomes_an_in_document_link(tmp_path, monkeypatch):
    """The member files are gone from the one-file deliverable; their ids are not."""
    data = _epub(
        {
            "chapter-1.xhtml": '<p id="p1"><a href="chapter-2.xhtml#illustration-3">see</a></p>',
            "chapter-2.xhtml": (
                '<section id="chapter-2"><p id="illustration-3">Fig.</p>'
                '<a href="chapter-1.xhtml#p1">back</a></section>'
            ),
        }
    )
    out = _normalize(tmp_path, monkeypatch, data)

    assert '<a href="#illustration-3">see</a>' in out
    assert '<a href="#p1">back</a>' in out
    assert ".xhtml#" not in out


def test_a_link_to_a_whole_member_points_at_the_anchor_its_section_starts_with(
    tmp_path, monkeypatch
):
    data = _epub(
        {
            "chapter-1.xhtml": '<p><a href="uncopyright.xhtml">Uncopyright</a></p>',
            "uncopyright.xhtml": '<section id="uncopyright"><h2>Uncopyright</h2></section>',
        }
    )
    out = _normalize(tmp_path, monkeypatch, data)

    assert '<a href="#uncopyright">Uncopyright</a>' in out


def test_a_link_to_a_member_that_starts_without_an_anchor_keeps_only_its_text(
    tmp_path, monkeypatch
):
    """An id met after the member's first text is not where the member starts."""
    data = _epub(
        {
            "chapter-1.xhtml": '<p><a class="next" href="chapter-2.xhtml">Next chapter</a></p>',
            "chapter-2.xhtml": '<p>Plain opening. <span id="later">Later.</span></p>',
        }
    )
    out = _normalize(tmp_path, monkeypatch, data)

    assert '<a class="next">Next chapter</a>' in out
    assert "#later" not in out


def test_links_resolve_against_the_linking_members_folder(tmp_path, monkeypatch):
    data = _epub(
        {
            "Text/ch1.xhtml": '<a href="../Text/ch%202.xhtml#x">one</a><a href="ch%202.xhtml#y">two</a>',
            "Text/ch 2.xhtml": '<h1 id="x">Two</h1><p id="y">Body.</p>',
        }
    )
    out = _normalize(tmp_path, monkeypatch, data)

    assert '<a href="#x">one</a>' in out and '<a href="#y">two</a>' in out


def test_a_link_to_a_member_the_deliverable_leaves_out_keeps_only_its_text(tmp_path, monkeypatch):
    """A navigation document off the spine and a member that was all boilerplate
    have no place in the deliverable to link to."""
    data = _epub(
        {
            "ch1.xhtml": (
                '<h1 id="c1">One</h1><a href="toc.xhtml#toc">Contents</a>'
                '<a href="license.xhtml">License</a><a href="license.xhtml#terms">Terms</a>'
            ),
            "license.xhtml": '<footer class="pg-boilerplate" id="terms">FULL LICENSE</footer>',
        },
        nav='<nav id="toc"><a href="ch1.xhtml#c1">One</a></nav>',
    )
    out = _normalize(tmp_path, monkeypatch, data)

    assert "FULL LICENSE" not in out and 'id="toc"' not in out
    assert "<a>Contents</a>" in out and "<a>License</a>" in out and "<a>Terms</a>" in out


def test_links_that_name_no_member_are_left_as_written(tmp_path, monkeypatch):
    hrefs = (
        "https://example.org/ch2.xhtml#x",
        "mailto:editor@example.org",
        "#local",
        "../Images/plate.png",
        "missing.xhtml#gone",
        "style.css",
    )
    body = '<p id="local">x</p>' + "".join(f'<a href="{h}">{i}</a>' for i, h in enumerate(hrefs))
    out = _normalize(tmp_path, monkeypatch, _epub({"Text/ch1.xhtml": body}))

    for h in hrefs:
        assert f'href="{h}"' in out


@pytest.mark.parametrize(
    ("fragment", "written"),
    [("caf%C3%A9", "#caf%C3%A9"), ("a&b", "#a&amp;b")],
    ids=["percent-encoded", "ampersand"],
)
def test_a_fragment_is_kept_as_written(tmp_path, monkeypatch, fragment, written):
    data = _epub({"ch1.xhtml": f'<a href="ch2.xhtml#{fragment}">go</a>', "ch2.xhtml": "<p>x</p>"})
    out = _normalize(tmp_path, monkeypatch, data)

    assert f'<a href="{written}">go</a>' in out


def test_pages_of_an_html_zip_link_to_each_other_inside_the_deliverable(tmp_path, monkeypatch):
    data = _zip(
        {
            "manual/guide/start.html": _page(
                '<h1 id="start">Start</h1><a href="../ref/options.html#verbose">verbose</a>'
            ),
            "manual/ref/options.html": _page(
                '<h1 id="options">Options</h1><dt id="verbose">-v</dt>'
            ),
        }
    )
    out = _normalize(tmp_path, monkeypatch, data, url="https://x.com/manual.zip")

    assert '<a href="#verbose">verbose</a>' in out


def test_a_link_lands_in_its_own_member_when_an_earlier_one_repeats_the_id(tmp_path, monkeypatch):
    """Pages of one site reuse section ids (Examples, Notes); joined into one file, the
    first holder of an id would catch every link meant for a later one."""
    data = _zip(
        {
            "docs/a-intro.html": _page(
                '<section id="examples"><p>intro examples</p></section>'
                '<a href="z-api.html#examples">to api</a><a href="#examples">to intro</a>'
            ),
            "docs/z-api.html": _page(
                '<section id="examples"><p>api examples</p></section><a href="#examples">to own</a>'
            ),
        }
    )
    soup = BeautifulSoup(
        _normalize(tmp_path, monkeypatch, data, url="https://x.com/docs.zip"), "html.parser"
    )

    ids = [el["id"] for el in soup.find_all(id=True)]
    assert len(ids) == len(set(ids))
    landing = {a.get_text(): soup.find(id=a["href"][1:]).get_text() for a in soup.find_all("a")}
    assert landing == {
        "to api": "api examples",
        "to intro": "intro examples",
        "to own": "api examples",
    }
