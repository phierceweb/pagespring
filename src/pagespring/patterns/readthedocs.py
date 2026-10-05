"""readthedocs: an RTD project's PDF build (``/_/downloads/[<alias>/]<lang>/<version>/pdf/``), else
a Sphinx crawl when that 404s; download and file URLs go to their own patterns."""

from __future__ import annotations

import re
import urllib.error
from pathlib import Path
from urllib.parse import urlparse

from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger
from pf_core.utils.slugify import slugify

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns import _pdf, _sphinx
from pagespring.patterns._file_url import owned_by_file_pattern
from pagespring.patterns.docs_probe import DocsProbePattern
from pagespring.patterns.pdf_url import PdfUrlPattern

log = get_logger(__name__)

_LANG_RE = re.compile(r"^[a-z]{2}(?:-[a-z]{2,4})?$")


def _lang_version(path: str) -> tuple[str, str]:
    """(lang, version) from an RTD URL path, defaulting to ("en", "latest")."""
    segs = [s for s in path.split("/") if s]
    if len(segs) >= 2 and _LANG_RE.match(segs[0]):
        return segs[0], segs[1]
    return "en", "latest"


def _subproject(path: str) -> tuple[str, str, str] | None:
    """(alias, lang, version) for a ``/projects/<alias>/…`` path, else None; an alias may span
    segments, up to the first language segment with a version after it."""
    segs = [s for s in path.split("/") if s]
    if len(segs) < 2 or segs[0] != "projects":
        return None
    rest = segs[1:]
    for i in range(1, len(rest) - 1):
        if _LANG_RE.match(rest[i]):
            return "/".join(rest[:i]), rest[i], rest[i + 1]
    return rest[0], "en", "latest"


class ReadTheDocsPattern:
    name = "readthedocs"

    def match(self, url: str) -> bool:
        p = urlparse(url)
        if "/_/downloads/" in p.path.lower() or owned_by_file_pattern(url):
            return False
        return p.netloc.lower().endswith(".readthedocs.io")

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        p = urlparse(url)
        host = p.netloc.lower()
        slug = host.split(".")[0]
        sub = _subproject(p.path)
        if sub is None:
            lang, version = _lang_version(p.path)
            dl = f"{p.scheme}://{host}/_/downloads/{lang}/{version}/pdf/"
            base = f"{p.scheme}://{host}/{lang}/{version}/"
        else:
            alias, lang, version = sub
            slug = slugify(f"{slug}-{alias}")
            dl = f"{p.scheme}://{host}/_/downloads/{alias}/{lang}/{version}/pdf/"
            base = f"{p.scheme}://{host}/projects/{alias}/{lang}/{version}/"
        try:
            _final, data = http.fetch_bytes(dl)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            log.info("readthedocs.no_pdf_build", download=dl, status=exc.code)
            return _sphinx.acquire(base, workdir, slug=slug, title=None)
        if not data.startswith(b"%PDF"):
            raise InvalidInputError(f"{dl} did not serve a PDF — unexpected RTD response")
        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        pdf = raw_dir / f"{slug}.pdf"
        pdf.write_bytes(data)
        pages = _pdf.page_count(pdf)
        log.info(
            "readthedocs.acquire", url=url, download=dl, slug=slug, bytes=len(data), pages=pages
        )
        return AcquireResult(raw_dir=raw_dir, kind="pdf", slug=slug, pages=pages)

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        if acq.kind == "pdf":
            return PdfUrlPattern().normalize(acq, workdir)
        # Sphinx-crawl fallback: same merge shape as docs_probe's html branch.
        return DocsProbePattern().normalize(acq, workdir)
