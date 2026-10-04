"""PDF page counting for patterns that stage a PDF; a real parser, since PDF 1.5+ keeps the page
tree in compressed object streams that a ``/Type /Page`` scan misses."""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
from pf_core.log import get_logger

log = get_logger(__name__)


def page_count(path: Path) -> int | None:
    """Pages in ``path``, or None when undeterminable: a damaged or encrypted PDF is still a valid
    deliverable, and a made-up count is worse than none."""
    try:
        return len(pdfium.PdfDocument(str(path)))
    except (pdfium.PdfiumError, OSError, ValueError) as exc:
        log.warning("pdf.unreadable", path=str(path), error=str(exc))
        return None
