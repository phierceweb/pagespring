"""_pdf_spreads — the 2-up spread detector and the box-only cut, on hand-built PDFs."""

from __future__ import annotations

import pypdfium2 as pdfium
import pytest

from pagespring.patterns import _pdf_spreads

COVER, SPREAD = (250, 320), (500, 320)


def _content(index: int, width: float, height: float) -> str:
    half = width / 2
    return (
        f"1 0 0 rg 0 0 {half} {height} re f 0 0 1 rg {half} 0 {half} {height} re f "
        f"0 g BT /F1 12 Tf 10 {height - 20} Td (L{index}) Tj ET "
        f"BT /F1 12 Tf {half + 10} {height - 20} Td (R{index}) Tj ET"
    )


def _pdf(
    sizes: list[tuple[float, float]],
    *,
    rotate: dict[int, int] | None = None,
    title: str | None = None,
    outline: bool = False,
    file_id: str | None = None,
    trailer_id: str | None = None,
) -> bytes:
    """A PDF whose every page paints its left half red and its right half blue, labelled ``L<i>``
    and ``R<i>``; hand-built so the fixture does not depend on the library under test."""
    n = len(sizes)
    pages = [4 + 2 * i for i in range(n)]
    extra = 4 + 2 * n
    catalog = "<</Type/Catalog/Pages 2 0 R" + (f"/Outlines {extra} 0 R" if outline else "") + ">>"
    objs = [
        catalog,
        f"<</Type/Pages/Kids[{' '.join(f'{p} 0 R' for p in pages)}]/Count {n}>>",
        "<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    for i, (w, h) in enumerate(sizes):
        stream = _content(i, w, h)
        turn = f"/Rotate {rotate[i]}" if rotate and i in rotate else ""
        objs.append(
            f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 {w} {h}]{turn}"
            f"/Resources<</Font<</F1 3 0 R>>>>/Contents {pages[i] + 1} 0 R>>"
        )
        objs.append(f"<</Length {len(stream)}>>stream\n{stream}\nendstream")
    if outline:
        objs.append(f"<</Type/Outlines/First {extra + 1} 0 R/Last {extra + 1} 0 R/Count 1>>")
        objs.append(f"<</Title(Getting Started)/Parent {extra} 0 R/Dest[{pages[1]} 0 R/Fit]>>")
    info = len(objs) + 1
    if title:
        objs.append(f"<</Title({title})>>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj {obj} endobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    trailer = f"/Size {len(objs) + 1}/Root 1 0 R" + (f"/Info {info} 0 R" if title else "")
    if file_id:
        trailer += f"/ID[<{file_id}><{file_id}>]"
    if trailer_id:
        trailer += f"/ID{trailer_id}"
    out += f"trailer <<{trailer}>>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _spreads(sizes, **kw) -> list[int]:
    return _pdf_spreads.spread_pages(pdfium.PdfDocument(_pdf(sizes, **kw)))


def _cut(tmp_path, sizes, **kw):
    src = tmp_path / "manual.pdf"
    src.write_bytes(_pdf(sizes, **kw))
    path, cut = _pdf_spreads.single_pages(src, tmp_path / "single.pdf")
    return src, path, cut


def _pixels(page: pdfium.PdfPage, crop=(0, 0, 0, 0)) -> tuple[int, int, bytes]:
    bitmap = page.render(scale=1, crop=crop)
    return bitmap.width, bitmap.height, bytes(bitmap.buffer)


def test_a_page_twice_the_narrowest_width_is_a_spread():
    assert _spreads([COVER, SPREAD, SPREAD, COVER]) == [1, 2]


def test_pages_of_one_width_hold_no_spread():
    """With every page as wide as the first, a uniformly wide PDF is never cut."""
    assert _spreads([SPREAD, SPREAD, SPREAD]) == []


def test_a_page_well_short_of_twice_the_width_is_not_a_spread():
    assert _spreads([COVER, (370, 320)]) == []


def test_a_narrow_insert_does_not_make_the_ordinary_pages_spreads():
    """A flap 0.6x a page's width would otherwise read every page as two."""
    assert _spreads([(150, 320), COVER, COVER, COVER]) == []


def test_a_narrow_rotated_page_does_not_anchor_the_width():
    """Displayed 100 wide, it would otherwise make every 250-wide page a spread."""
    assert _spreads([(320, 100), COVER, COVER], rotate={0: 90}) == []


def test_a_spread_measures_against_the_first_unrotated_page():
    assert _spreads([(320, 100), COVER, SPREAD], rotate={0: 90}) == [2]


def test_a_half_width_insert_does_not_make_the_ordinary_pages_spreads():
    """One tear-out card would otherwise read every letter page as two."""
    assert _spreads([(612, 792)] * 3 + [(306, 792)] + [(612, 792)] * 3) == []


def test_a_pdf_that_opens_on_a_spread_is_not_cut():
    """With a wide first page there is no single page to measure against."""
    assert _spreads([SPREAD, COVER, SPREAD, COVER]) == []


def test_a_landscape_page_among_portrait_ones_is_not_a_spread():
    assert _spreads([(612, 792), (792, 612), (612, 792)]) == []


def test_a_wide_page_of_another_height_is_not_a_spread():
    """A 16:9 slide twice a portrait page's width is still not two pages side by side."""
    assert _spreads([(612, 792), (1224, 689)]) == []


def test_a_rotated_page_is_not_a_spread():
    """Its media box is cut along the unrotated axis, which is not the fold a reader sees."""
    assert _spreads([COVER, (320, 500)], rotate={1: 90}) == []


def test_each_spread_becomes_its_left_then_its_right_page(tmp_path):
    _src, path, cut = _cut(tmp_path, [COVER, SPREAD, SPREAD, COVER])

    pdf = pdfium.PdfDocument(path)
    assert cut == 2
    boxes = [tuple(round(v) for v in pdf[i].get_mediabox()) for i in range(len(pdf))]
    assert boxes == [
        (0, 0, 250, 320),
        (0, 0, 250, 320),
        (250, 0, 500, 320),
        (0, 0, 250, 320),
        (250, 0, 500, 320),
        (0, 0, 250, 320),
    ]
    assert [tuple(pdf[i].get_cropbox()) for i in range(len(pdf))] == [
        tuple(pdf[i].get_mediabox()) for i in range(len(pdf))
    ]


def test_each_half_reads_only_its_own_text(tmp_path):
    _src, path, _cut_count = _cut(tmp_path, [COVER, SPREAD, SPREAD, COVER])

    pdf = pdfium.PdfDocument(path)
    text = [pdf[i].get_textpage().get_text_bounded().strip() for i in range(len(pdf))]
    assert text[1:5] == ["L1", "R1", "L2", "R2"]


def test_each_half_renders_exactly_as_it_did_on_the_spread(tmp_path):
    src, path, _cut_count = _cut(tmp_path, [COVER, SPREAD, COVER])

    spread = pdfium.PdfDocument(src)[1]
    single = pdfium.PdfDocument(path)
    assert _pixels(single[1]) == _pixels(spread, crop=(0, 0, 250, 0))
    assert _pixels(single[2]) == _pixels(spread, crop=(250, 0, 0, 0))


def test_the_cut_keeps_the_outline_and_the_document_info(tmp_path):
    _src, path, cut = _cut(tmp_path, [COVER, SPREAD, COVER], outline=True, title="Widget Manual")

    pdf = pdfium.PdfDocument(path)
    assert cut == 1
    [item] = list(pdf.get_toc())
    assert item.get_title() == "Getting Started"
    assert item.get_dest().get_index() == 1  # the spread's left half
    assert pdf.get_metadata_value("Title") == "Widget Manual"


def test_the_cut_is_byte_reproducible(tmp_path):
    """The staged sha256 is the deliverable's identity; an unchanged source must not read as new."""
    src, first, cut = _cut(tmp_path, [COVER, SPREAD, COVER])
    first_bytes = first.read_bytes()

    again, _ = _pdf_spreads.single_pages(src, tmp_path / "again.pdf")

    assert cut == 1
    assert again.read_bytes() == first_bytes


def test_the_cut_keeps_the_source_file_identifier(tmp_path):
    src, path, cut = _cut(
        tmp_path, [COVER, SPREAD, COVER], file_id="0123456789ABCDEF0123456789ABCDEF"
    )

    assert cut == 1
    assert pdfium.PdfDocument(path).get_identifier(0) == pdfium.PdfDocument(src).get_identifier(0)


@pytest.mark.parametrize(
    "trailer_id",
    ["[(manual-v1)(manual-v1)]", "[<><0123456789ABCDEF>]"],
    ids=["literal", "empty-hex"],
)
def test_a_source_file_id_in_another_form_still_cuts_reproducibly(tmp_path, trailer_id):
    _src, path, cut = _cut(tmp_path, [COVER, SPREAD, COVER], trailer_id=trailer_id)
    first = path.read_bytes()
    _src, again, _cut_again = _cut(tmp_path, [COVER, SPREAD, COVER], trailer_id=trailer_id)

    assert cut == 1
    assert again.read_bytes() == first


def test_a_pdf_without_spreads_is_returned_as_is(tmp_path):
    src, path, cut = _cut(tmp_path, [COVER, COVER])

    assert (path, cut) == (src, 0)
    assert not (tmp_path / "single.pdf").exists()


def test_an_unreadable_pdf_is_returned_as_is(tmp_path):
    src = tmp_path / "broken.pdf"
    src.write_bytes(b"%PDF-1.7\n" + b"garbage that is not a page tree\n" * 20)

    assert _pdf_spreads.single_pages(src, tmp_path / "single.pdf") == (src, 0)


def test_a_cut_pdfium_refuses_leaves_the_spread_pdf(tmp_path, monkeypatch):
    """The spread PDF is still a valid deliverable; a failed cut must not fail the ingest."""
    monkeypatch.setattr(_pdf_spreads.pdfium_c, "FPDF_MovePages", lambda *a: 0)

    src, path, cut = _cut(tmp_path, [COVER, SPREAD, COVER])

    assert (path, cut) == (src, 0)
    assert not (tmp_path / "single.pdf").exists()


def test_a_cut_that_saves_differently_each_time_leaves_the_spread_pdf(tmp_path, monkeypatch):
    """pdfium re-encrypts an AES document with fresh IVs on every save; such a cut would
    re-stage an unchanged source on every --if-changed run."""
    save, saves = pdfium.PdfDocument.save, []

    def varying_save(self, dest, **kw):
        save(self, dest, **kw)
        saves.append(1)
        dest.write(b"%" + str(len(saves)).encode() + b"\n")

    monkeypatch.setattr(pdfium.PdfDocument, "save", varying_save)

    src, path, cut = _cut(tmp_path, [COVER, SPREAD, COVER])

    assert (path, cut) == (src, 0)
    assert not (tmp_path / "single.pdf").exists()
