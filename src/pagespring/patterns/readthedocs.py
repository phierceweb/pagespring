"""readthedocs — manuals hosted on Read the Docs (``*.readthedocs.io``).

RTD projects publish downloadable builds at
``https://<proj>.readthedocs.io/_/downloads/<lang>/<version>/pdf/`` (an
extensionless URL that serves the PDF); a subproject served at
``/projects/<alias>/<lang>/<version>/`` publishes its own at
``/_/downloads/<alias>/<lang>/<version>/pdf/``. acquire derives the alias,
language and version from the page URL (default ``en/latest``), downloads that
build, and passes the PDF through — the same deliverable shape as pdf_url. A 404
at the download URL (no build published) falls back to a Sphinx crawl of the
rendered docs; any other fetch failure propagates (exit 4).

Declined, so the patterns that own them claim them: any path under
``/_/downloads/``, and a URL naming a PDF, API spec or archive file.
"""

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
from pagespring.patterns.archive_download import ArchiveDownloadPattern
from pagespring.patterns.docs_probe import DocsProbePattern
from pagespring.patterns.pdf_url import PdfUrlPattern

log = get_logger(__name__)

_LANG_RE = re.compile(r"^[a-z]{2}(?:-[a-z]{2,4})?$")
# Suffixes only: api_spec's name tokens would also claim a docs page like openapi.html.
_SPEC_SUFFIXES = (".json", ".yaml", ".yml")


def _lang_version(path: str) -> tuple[str, str]:
    """(lang, version) from an RTD URL path, defaulting to ("en", "latest")."""
    segs = [s for s in path.split("/") if s]
    if len(segs) >= 2 and _LANG_RE.match(segs[0]):
        return segs[0], segs[1]
    return "en", "latest"


def _subproject(path: str) -> tuple[str, str, str] | None:
    """(alias, lang, version) for a ``/projects/<alias>/…`` path, else None.

    An alias may span segments (``api/python``); it runs up to the first
    language segment that has a version after it.
    """
    segs = [s for s in path.split("/") if s]
    if len(segs) < 2 or segs[0] != "projects":
        return None
    rest = segs[1:]
    for i in range(1, len(rest) - 1):
        if _LANG_RE.match(rest[i]):
            return "/".join(rest[:i]), rest[i], rest[i + 1]
    return rest[0], "en", "latest"


def _names_a_file(url: str) -> bool:
    path = urlparse(url).path.lower()
    return (
        path.endswith(_SPEC_SUFFIXES)
        or ArchiveDownloadPattern().match(url)
        or PdfUrlPattern().match(url)
    )


class ReadTheDocsPattern:
    name = "readthedocs"

    def match(self, url: str) -> bool:
        p = urlparse(url)
        if "/_/downloads/" in p.path.lower() or _names_a_file(url):
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
                raise  # fetch failed — orchestrate reports it honestly (exit 4)
            # No PDF build published — crawl the rendered docs instead.
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
            # Passthrough: the downloaded PDF is already a pagespeak input.
            return next(acq.raw_dir.glob("*.pdf"))
        # Sphinx-crawl fallback: same merge shape as docs_probe's html branch.
        return DocsProbePattern().normalize(acq, workdir)
