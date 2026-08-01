"""PDF page counting, shared by the patterns that stage a PDF deliverable.

A real parser is required: PDF 1.5+ keeps the page tree in compressed object
streams, so scanning for ``/Type /Page`` both misses and double-counts.
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
from pf_core.log import get_logger

log = get_logger(__name__)


def page_count(path: Path) -> int | None:
    """Number of pages in ``path``, or None when it cannot be determined.

    None rather than a guess: a damaged or password-protected PDF is still a
    valid deliverable, and a fabricated count is worse than an absent one.
    """
    try:
        return len(pdfium.PdfDocument(str(path)))
    except (pdfium.PdfiumError, OSError, ValueError) as exc:
        log.warning("pdf.unreadable", path=str(path), error=str(exc))
        return None
