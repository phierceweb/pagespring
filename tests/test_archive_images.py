"""Archive members a deliverable references are bundled into ``images/`` and staged."""

import io
import json
import zipfile

import pytest

from pagespring import _integrity, audit, http, images, manifest, orchestrate, renormalize
from pagespring.patterns.archive_download import ArchiveDownloadPattern

FIG_A = b"\x89PNG\r\n\x1a\nfigure-a"
FIG_B = b"\x89PNG\r\n\x1a\nfigure-b"
COVER = b"\xff\xd8\xffcover"

_OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>{items}</manifest>
  <spine>{refs}</spine>
</package>"""

_SVG_COVER = (
    '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
    '<image width="10" height="10" xlink:href="{href}"/></svg>'
)


@pytest.fixture(autouse=True)
def _incoming_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate.cfg, "INCOMING_DIR", str(tmp_path / "incoming"))


def _serve(monkeypatch, data: bytes) -> None:
    meta = {"etag": None, "last_modified": None}
    monkeypatch.setattr(http, "fetch_bytes_meta", lambda url, **kw: (url, data, meta))


def _epub(chapters: dict[str, str], files: dict[str, bytes] | None = None) -> bytes:
    """An EPUB under ``OEBPS/`` whose spine lists ``chapters`` (href -> body) in order."""
    items = "".join(
        f'<item id="c{i}" href="{href}" media-type="application/xhtml+xml"/>'
        for i, href in enumerate(chapters)
    )
    refs = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("OEBPS/content.opf", _OPF.format(items=items, refs=refs))
        for href, body in chapters.items():
            z.writestr(
                f"OEBPS/{href}",
                f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>',
            )
        for name, data in (files or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def _zip(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _normalize(tmp_path, monkeypatch, data: bytes, url: str = "https://x.com/book.epub"):
    _serve(monkeypatch, data)
    p = ArchiveDownloadPattern()
    work = tmp_path / "work"
    out = p.normalize(p.acquire(url, work), work)
    return out.read_text(encoding="utf-8"), work / "images"


def _illustrated_epub() -> bytes:
    return _epub(
        {
            "wrap0000.xhtml": _SVG_COVER.format(href="cover.jpg"),
            "Text/ch1.xhtml": '<h1>One</h1><img alt="A" src="../Images/Fig-A.PNG"/>',
            "Text/ch2.xhtml": '<h1>Two</h1><img alt="B" src="../Images/fig%20b.png"/>',
        },
        {
            "OEBPS/cover.jpg": COVER,
            "OEBPS/Images/Fig-A.PNG": FIG_A,
            "OEBPS/Images/fig b.png": FIG_B,
        },
    )


def test_referenced_members_are_copied_beside_the_deliverable_and_re_pointed(tmp_path, monkeypatch):
    """The extracted archive is discarded after normalize, so a ref left naming a
    member path renders as a broken image and no check can see it."""
    out, bundle = _normalize(tmp_path, monkeypatch, _illustrated_epub())

    assert 'src="images/fig-a.png"' in out
    assert 'src="images/fig-b.png"' in out
    assert 'xlink:href="images/cover.jpg"' in out, "the SVG cover kept its archive path"
    assert "../Images" not in out
    assert (bundle / "fig-a.png").read_bytes() == FIG_A
    assert (bundle / "fig-b.png").read_bytes() == FIG_B
    assert (bundle / "cover.jpg").read_bytes() == COVER


def test_a_re_pointed_img_drops_the_srcset_naming_archive_paths(tmp_path, monkeypatch):
    """A renderer prefers srcset to src, so candidates left naming archive paths
    would break the figure the re-pointed src restores."""
    img = (
        '<img src="../Images/fig.png" sizes="50vw" '
        'srcset="../Images/fig-2x.png 2x, ../Images/fig.png 1x"/>'
    )
    data = _epub(
        {"Text/ch1.xhtml": img},
        {"OEBPS/Images/fig.png": FIG_A, "OEBPS/Images/fig-2x.png": FIG_B},
    )
    out, bundle = _normalize(tmp_path, monkeypatch, data)

    assert 'src="images/fig.png"' in out
    assert "srcset" not in out and "sizes" not in out
    assert [p.name for p in bundle.iterdir()] == ["fig.png"]


def test_members_sharing_a_basename_keep_distinct_copies(tmp_path, monkeypatch):
    data = _epub(
        {
            "part1/ch.xhtml": '<img src="fig.png"/>',
            "part2/ch.xhtml": '<img src="fig.png"/>',
        },
        {"OEBPS/part1/fig.png": FIG_A, "OEBPS/part2/fig.png": FIG_B},
    )
    out, bundle = _normalize(tmp_path, monkeypatch, data)

    assert out.index('src="images/fig.png"') < out.index('src="images/fig-2.png"')
    assert (bundle / "fig.png").read_bytes() == FIG_A
    assert (bundle / "fig-2.png").read_bytes() == FIG_B


def test_a_member_referenced_twice_is_copied_once(tmp_path, monkeypatch):
    data = _epub(
        {
            "Text/a.xhtml": '<img src="../shared/logo.png"/>',
            "Text/b.xhtml": '<img src="../shared/logo.png#x"/>',
        },
        {"OEBPS/shared/logo.png": FIG_A},
    )
    out, bundle = _normalize(tmp_path, monkeypatch, data)

    assert out.count('src="images/logo.png"') == 2
    assert [p.name for p in bundle.iterdir()] == ["logo.png"]


def test_refs_that_name_no_archive_path_are_left_as_written(tmp_path, monkeypatch):
    refs = (
        "https://cdn.example.com/remote.png",
        "//cdn.example.com/relative-scheme.png",
        "data:image/png;base64,iVBORw0KGgo=",
    )
    data = _epub({"ch.xhtml": "".join(f'<img src="{r}"/>' for r in refs)})
    out, bundle = _normalize(tmp_path, monkeypatch, data)

    for ref in refs:
        assert f'src="{ref}"' in out
    assert not bundle.exists(), "nothing in the archive was referenced"


def test_a_ref_escaping_the_archive_copies_nothing_from_outside_it(tmp_path, monkeypatch):
    """The member sits in work/raw/OEBPS/, so three levels up is tmp_path."""
    (tmp_path / "secret.png").write_bytes(b"not part of the archive")
    data = _epub({"ch.xhtml": '<img src="../../../secret.png"/>'})
    out, bundle = _normalize(tmp_path, monkeypatch, data)

    assert 'src="images/secret.png"' in out
    assert not (bundle / "secret.png").exists()


def test_markdown_refs_to_archive_members_are_bundled(tmp_path, monkeypatch):
    """A ref in a code block stays as written, whether or not the archive holds it."""
    data = _zip(
        {
            "docs/guide.md": (
                "# Guide\n\n![Arch](img/Arch%20Diagram.png)\n\n"
                '<img alt="logo" src="img/logo.svg">\n\n'
                "```\n![gone](img/missing.png)\n```\n"
            ),
            "docs/img/Arch Diagram.png": FIG_A,
            "docs/img/logo.svg": b"<svg/>",
        }
    )
    out, bundle = _normalize(tmp_path, monkeypatch, data, url="https://x.com/docs.zip")

    assert "![Arch](images/arch-diagram.png)" in out
    assert 'src="images/logo.svg"' in out
    assert "![gone](img/missing.png)" in out
    assert (bundle / "arch-diagram.png").read_bytes() == FIG_A


def test_markdown_code_keeps_its_refs_as_written(tmp_path, monkeypatch):
    data = _zip(
        {
            "docs/guide.md": (
                "# Guide\n\n```md\n![A](img/a.png)\n```\n\n"
                "Write `![A](img/a.png)` to embed it.\n\n![A](img/a.png)\n"
            ),
            "docs/img/a.png": FIG_A,
        }
    )
    out, _bundle = _normalize(tmp_path, monkeypatch, data, url="https://x.com/docs.zip")

    assert out.count("![A](img/a.png)") == 2, "a code sample's ref was re-pointed"
    assert out.count("![A](images/a.png)") == 1


def test_a_markdown_ref_to_a_member_the_archive_lacks_is_reported_by_audit(tmp_path, monkeypatch):
    _serve(
        monkeypatch,
        _zip({"docs/guide.md": "# Guide\n\n![gone](img/missing.png)\n", "docs/b.md": "# B\n"}),
    )
    orchestrate.run_ingest("https://x.com/docs.zip")

    checks = {(f["check"], f["level"]) for f in audit.audit_slug("docs")}
    assert ("broken_image_ref", "error") in checks


def test_rst_image_and_figure_directives_are_bundled(tmp_path, monkeypatch):
    data = _zip(
        {
            "docs/guide.rst": (
                "Guide\n=====\n\n.. image:: img/a.png\n   :alt: A\n\n"
                ".. figure:: ../shared/b.png\n\n   Caption\n\n"
                ".. image:: https://cdn.example.com/remote.png\n"
            ),
            "docs/img/a.png": FIG_A,
            "shared/b.png": FIG_B,
        }
    )
    out, bundle = _normalize(tmp_path, monkeypatch, data, url="https://x.com/docs.zip")

    assert ".. image:: images/a.png\n   :alt: A" in out
    assert ".. figure:: images/b.png" in out
    assert ".. image:: https://cdn.example.com/remote.png" in out
    assert (bundle / "a.png").read_bytes() == FIG_A
    assert (bundle / "b.png").read_bytes() == FIG_B


def _slug_dir(tmp_path):
    return tmp_path / "incoming" / "book"


def test_an_illustrated_epub_stages_its_figures_and_audits_clean(tmp_path, monkeypatch):
    _serve(monkeypatch, _illustrated_epub())
    res = orchestrate.run_ingest("https://x.com/book.epub")

    slug_dir = _slug_dir(tmp_path)
    assert sorted(p.name for p in (slug_dir / "images").iterdir()) == [
        "cover.jpg",
        "fig-a.png",
        "fig-b.png",
    ]
    assert (slug_dir / "images" / "fig-a.png").read_bytes() == FIG_A
    assert res["images"] == 3
    m = manifest.read_manifest(slug_dir)
    assert m["images"] == 3
    assert audit.audit_slug("book") == []


def test_damage_to_a_bundled_deliverable_is_an_error(tmp_path, monkeypatch):
    """Local refs are what an image pass leaves, so a bundle without a recorded hash
    would read as an unverifiable localize: damage would pass as a warning, and an
    unchanged re-fetch would keep the damaged file."""
    _serve(monkeypatch, _illustrated_epub())
    orchestrate.run_ingest("https://x.com/book.epub")
    deliverable = _slug_dir(tmp_path) / "book.html"
    deliverable.write_text(deliverable.read_text(encoding="utf-8") + "junk", encoding="utf-8")

    checks = {(f["check"], f["level"]) for f in audit.audit_slug("book")}
    assert ("sha_mismatch", "error") in checks
    m = manifest.read_manifest(_slug_dir(tmp_path))
    assert _integrity.integrity(_slug_dir(tmp_path), m) == "damaged"
    again = orchestrate.run_ingest("https://x.com/book.epub", if_changed=True)
    assert again["changed"] is True, "the damaged file was kept as unchanged"


@pytest.mark.parametrize(
    "body",
    [
        '<img src="../Images/missing.png"/>',
        _SVG_COVER.format(href="missing-cover.jpg"),
    ],
    ids=["img", "svg-image"],
)
def test_a_member_the_archive_lacks_is_reported_by_audit(tmp_path, monkeypatch, body):
    _serve(monkeypatch, _epub({"Text/ch1.xhtml": f"<p>One</p>{body}"}))
    orchestrate.run_ingest("https://x.com/book.epub")

    checks = {(f["check"], f["level"]) for f in audit.audit_slug("book")}
    assert ("broken_image_ref", "error") in checks


def test_a_reingest_drops_figures_the_new_edition_no_longer_references(tmp_path, monkeypatch):
    first = _epub(
        {"ch.xhtml": '<img src="a.png"/><img src="b.png"/>'},
        {"OEBPS/a.png": FIG_A, "OEBPS/b.png": FIG_B},
    )
    second = _epub({"ch.xhtml": '<img src="a.png"/>'}, {"OEBPS/a.png": FIG_A})
    _serve(monkeypatch, first)
    orchestrate.run_ingest("https://x.com/book.epub")
    _serve(monkeypatch, second)
    res = orchestrate.run_ingest("https://x.com/book.epub")

    assert [p.name for p in (_slug_dir(tmp_path) / "images").iterdir()] == ["a.png"]
    assert res["images"] == 1
    assert audit.audit_slug("book") == []


def test_a_bundled_member_takes_over_a_localized_image_of_the_same_name(tmp_path, monkeypatch):
    """The bundle overwrites the file, so a localize record under its name would
    re-point a remote ref at the archive's image."""
    data = _epub(
        {"ch.xhtml": '<img src="fig.png"/><img src="https://cdn.example.com/other.png"/>'},
        {"OEBPS/fig.png": FIG_A},
    )
    _serve(monkeypatch, data)
    orchestrate.run_ingest("https://x.com/book.epub")
    slug_dir = _slug_dir(tmp_path)
    images.write_sidecar(
        slug_dir,
        [
            {
                "local": "fig.png",
                "source_url": "https://cdn.example.com/fig.png",
                "etag": None,
                "last_modified": None,
                "sha256": "0" * 64,
                "bytes": 1,
            }
        ],
    )
    orchestrate.run_ingest("https://x.com/book.epub")

    sidecar = json.loads((slug_dir / images.SIDECAR_NAME).read_text(encoding="utf-8"))
    assert sidecar["images"] == []
    assert (slug_dir / "images" / "fig.png").read_bytes() == FIG_A


def test_renormalize_rebundles_from_kept_raw(tmp_path, monkeypatch):
    _serve(monkeypatch, _illustrated_epub())
    orchestrate.run_ingest("https://x.com/book.epub", keep_raw=True)
    slug_dir = _slug_dir(tmp_path)
    chapter = slug_dir / "raw" / "OEBPS" / "Text" / "ch1.xhtml"
    chapter.write_text(
        chapter.read_text(encoding="utf-8").replace("One", "One, revised"), encoding="utf-8"
    )

    res = renormalize.run_renormalize("book")

    assert res["changed"] is True
    assert (slug_dir / "images" / "fig-a.png").read_bytes() == FIG_A
    m = manifest.read_manifest(slug_dir)
    assert m["images"] == 3
    assert audit.audit_slug("book") == []
