"""pdf_url — a direct link to a PDF manual.

acquire: download the PDF. normalize: pass it through unchanged (a PDF is already
a pagespeak input). Covers vendor gear manuals (direct ``.pdf`` links) and
Read-the-Docs PDF builds (``…/_/downloads/en/<ver>/pdf/``, which serve a PDF at
an extensionless path).
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from pf_core.log import get_logger
from pf_core.utils.slugify import slugify

from pagespring import http
from pagespring.base import AcquireResult

log = get_logger(__name__)


def _slugify(name: str) -> str:
    name = re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE)
    return slugify(name) or "manual"


def _slug_from_url(url: str) -> str:
    p = urlparse(url)
    name = unquote(Path(p.path).name)
    if name.lower().endswith(".pdf"):
        return _slugify(name)
    # RTD-style /_/downloads/en/<ver>/pdf/ — basename is "pdf"; name from the host.
    return _slugify(p.netloc.lower().removeprefix("www.").split(".")[0])


class PdfUrlPattern:
    name = "pdf_url"

    # Vendor PDF manuals rarely carry a usable heading outline, so llm_full
    # fixes levels; split for RAG.
    convert_recipe = [
        "--normalize-headings",
        "--normalize-headings-mode",
        "llm_full",
        "--split-sections",
    ]

    def match(self, url: str) -> bool:
        path = urlparse(url).path.lower().rstrip("/")
        return path.endswith(".pdf") or path.endswith("/pdf")  # .pdf or RTD /pdf/

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        slug = _slug_from_url(url)
        _final, data = http.fetch_bytes(url)
        (raw_dir / f"{slug}.pdf").write_bytes(data)
        log.info("pdf_url.acquire", url=url, slug=slug, bytes=len(data))
        return AcquireResult(raw_dir=raw_dir, kind="pdf", slug=slug, pages=1)

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        # Passthrough: the downloaded PDF is already a pagespeak input.
        return next(acq.raw_dir.glob("*.pdf"))
