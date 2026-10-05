"""Cut a 2-up spread PDF (two printed pages side by side per sheet) into single pages by page box
alone: no object is removed or rewritten, so each half renders exactly as it did on the spread."""

from __future__ import annotations

import ctypes
import io
import re
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from pf_core.log import get_logger
from pf_core.pipeline.run_record import file_sha256

log = get_logger(__name__)

# A spread is about twice the first unrotated page's width (the cover's), at that page's height.
_SPREAD_WIDTH_MIN = 1.8
_SPREAD_WIDTH_MAX = 2.2
_HEIGHT_TOLERANCE = 0.02
# pdfium keeps the source's first /ID element as written (hex or literal) and writes a random hex
# second one.
_TRAILER_ID_RE = re.compile(
    rb"/ID\s*\[\s*(<[0-9A-Fa-f]*>|\((?:\\.|[^\\()])*\))\s*<([0-9A-Fa-f]+)>\s*\]"
)


def spread_pages(pdf: pdfium.PdfDocument) -> list[int]:
    """Indices of the unrotated pages about twice as wide as the first unrotated page, and as tall;
    one odd-sized insert elsewhere cannot set the measure."""
    shapes = []
    for i in range(len(pdf)):
        page = pdf[i]
        width, height = page.get_size()
        shapes.append((width, height, page.get_rotation()))
    upright = [(width, height) for width, height, rotation in shapes if rotation == 0]
    if not upright:
        return []
    narrow_w, narrow_h = upright[0]
    return [
        i
        for i, (width, height, rotation) in enumerate(shapes)
        if rotation == 0
        and narrow_w * _SPREAD_WIDTH_MIN <= width <= narrow_w * _SPREAD_WIDTH_MAX
        and abs(height - narrow_h) <= narrow_h * _HEIGHT_TOLERANCE
    ]


def single_pages(src: Path, out: Path) -> tuple[Path, int]:
    """Write ``src`` to ``out`` with each spread cut into its left and right page. Returns ``out``
    and the spreads cut, or ``src`` and 0 when there are none or pdfium can't read or repeat it."""
    try:
        pdf = pdfium.PdfDocument(str(src))
    except (pdfium.PdfiumError, OSError, ValueError) as exc:
        log.debug("pdf_spreads.unreadable", path=str(src), error=str(exc))
        return src, 0
    try:
        spreads = spread_pages(pdf)
        if not spreads:
            return src, 0
        _cut(pdf, spreads)
        seed = file_sha256(src)
        first, second = (_pin_file_id(_saved(pdf), seed=seed) for _ in range(2))
    except pdfium.PdfiumError as exc:
        log.warning("pdf_spreads.cut_failed", path=str(src), error=str(exc))
        return src, 0
    finally:
        pdf.close()
    if first != second:
        # AES re-encryption draws fresh IVs on every save; an /ID left unpinned differs too.
        log.info("pdf_spreads.not_reproducible", path=str(src))
        return src, 0
    out.write_bytes(first)
    log.info("pdf_spreads.cut", path=str(src), spreads=len(spreads))
    return out, len(spreads)


def _saved(pdf: pdfium.PdfDocument) -> bytes:
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _cut(pdf: pdfium.PdfDocument, spreads: list[int]) -> None:
    n = len(pdf)
    pdf.import_pages(pdf, spreads)  # one import, so the copies share one set of fonts and images
    for k, i in enumerate(spreads):
        # Copy k waits at n + k; the k copies already placed sit before spread i.
        moved = (ctypes.c_int * 1)(n + k)
        if not pdfium_c.FPDF_MovePages(pdf.raw, moved, 1, i + k + 1):
            raise pdfium.PdfiumError(f"could not place the copy of page {i}")
    for k, i in enumerate(spreads):
        left, right = pdf[i + k], pdf[i + k + 1]
        x0, y0, x1, y1 = left.get_bbox()
        fold = (x0 + x1) / 2
        for page, box in ((left, (x0, y0, fold, y1)), (right, (fold, y0, x1, y1))):
            page.set_mediabox(*box)
            page.set_cropbox(*box)


def _pin_file_id(data: bytes, *, seed: str) -> bytes:
    """Replace the /ID halves pdfium randomizes on save with ``seed`` digits, so one source always
    cuts to the same bytes; a permanent ID carried over from the source is kept."""
    found = list(_TRAILER_ID_RE.finditer(data))
    if not found:
        log.warning("pdf_spreads.file_id_unpinned")
        return data
    match = found[-1]
    permanent, changing = match.group(1), match.group(2)

    def digits(length: int) -> bytes:
        return (seed * (length // len(seed) + 1))[:length].upper().encode()

    # pdfium repeats one random ID in both halves when the source carried none.
    first = b"<" + digits(len(changing)) + b">" if permanent[1:-1] == changing else permanent
    pinned = b"/ID[" + first + b"<" + digits(len(changing)) + b">]"
    return data[: match.start()] + pinned + data[match.end() :]
