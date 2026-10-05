"""pdf_url: a direct PDF link, or a Read the Docs ``/_/downloads/…/pdf/`` build, passed through as
the deliverable; other extensionless URLs are docs_probe's to sniff."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger
from pf_core.utils.slugify import slugify
from pf_core.utils.url_parse import domain_of

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns import _pdf, _pdf_spreads

log = get_logger(__name__)

# Some servers prepend whitespace/BOM, so scan a window rather than the first byte.
_PDF_MAGIC = b"%PDF-"
_MAGIC_WINDOW = 1024
_RTD_DOWNLOAD_RE = re.compile(r"/_/downloads/(?:(?P<alias>.+)/)?[^/]+/[^/]+/pdf$")


def _slugify(name: str) -> str:
    name = re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE)
    return slugify(name) or "manual"


def _slug_from_url(url: str) -> str:
    p = urlparse(url)
    name = unquote(Path(p.path).name)
    if name.lower().endswith(".pdf"):
        return _slugify(name)
    # RTD-style /_/downloads/…/pdf/ — basename is "pdf"; name from the host.
    host = domain_of(url).split(".")[0]
    rtd = _RTD_DOWNLOAD_RE.search(p.path.rstrip("/"))
    if rtd and rtd.group("alias"):
        return _slugify(f"{host}-{rtd.group('alias')}")
    return _slugify(host)


class PdfUrlPattern:
    name = "pdf_url"
    single_fetch = True  # one-URL source; refresh may probe its stored validators

    def match(self, url: str) -> bool:
        path = urlparse(url).path.lower().rstrip("/")
        return path.endswith(".pdf") or _RTD_DOWNLOAD_RE.search(path) is not None

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        slug = _slug_from_url(url)
        _final, data, meta = http.fetch_bytes_meta(url)
        # A "PDF" URL that redirects to an HTML page still answers 200, and audit never
        # content-checks a kind:pdf deliverable, so check the magic bytes here.
        if _PDF_MAGIC not in data[:_MAGIC_WINDOW]:
            raise InvalidInputError(
                f"{url} returned {len(data)} bytes that are not a PDF "
                f"(starts {data[:16]!r}) — the URL probably redirects to an HTML page."
            )
        pdf = raw_dir / f"{slug}.pdf"
        pdf.write_bytes(data)
        pages = _pdf.page_count(pdf)
        log.info("pdf_url.acquire", url=url, slug=slug, bytes=len(data), pages=pages)
        return AcquireResult(
            raw_dir=raw_dir,
            kind="pdf",
            slug=slug,
            pages=pages,
            etag=meta["etag"],
            last_modified=meta["last_modified"],
        )

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        pdf = next(acq.raw_dir.glob("*.pdf"))
        clean, acq.spreads_split = _pdf_spreads.single_pages(pdf, workdir / pdf.name)
        if acq.pages is not None:  # a replay seeds pages from the manifest; count what ships
            acq.pages = _pdf.page_count(clean)
        return clean
