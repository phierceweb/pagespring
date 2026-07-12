"""readthedocs — manuals hosted on Read the Docs (``*.readthedocs.io``).

RTD projects publish downloadable builds at
``https://<proj>.readthedocs.io/_/downloads/<lang>/<version>/pdf/`` (an
extensionless URL that serves the PDF). acquire derives ``<lang>/<version>``
from the page URL (default ``en/latest``), downloads that build, and passes the
PDF through — the same deliverable shape as pdf_url. A 404 at the download URL
(no build published) falls back to a Sphinx crawl of the rendered docs; any
other fetch failure propagates (exit 4).

Explicit download URLs (path ending ``.pdf``/``/pdf``, or any path under
``/_/downloads/``) are declined so they keep routing to pdf_url unchanged.

**Note on convert_recipe:** The recipe is PDF-tuned (``--normalize-headings …
llm_full``). The HTML fallback path tolerates this recipe without issues; both
paths share the same downstream convert treatment.
"""

from __future__ import annotations

import re
import urllib.error
from pathlib import Path
from urllib.parse import urlparse

from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns import _sphinx
from pagespring.patterns.docs_probe import DocsProbePattern

log = get_logger(__name__)

_LANG_RE = re.compile(r"^[a-z]{2}(?:-[a-z]{2,4})?$")


def _lang_version(path: str) -> tuple[str, str]:
    """(lang, version) from an RTD URL path, defaulting to ("en", "latest")."""
    segs = [s for s in path.split("/") if s]
    if len(segs) >= 2 and _LANG_RE.match(segs[0]):
        return segs[0], segs[1]
    return "en", "latest"


class ReadTheDocsPattern:
    name = "readthedocs"

    # Same downstream treatment as pdf_url: PDF manuals need heading repair + split.
    convert_recipe = [
        "--normalize-headings",
        "--normalize-headings-mode",
        "llm_full",
        "--split-sections",
    ]

    def match(self, url: str) -> bool:
        p = urlparse(url)
        path = p.path.lower().rstrip("/")
        if path.endswith(".pdf") or path.endswith("/pdf"):
            return False  # explicit download links keep routing to pdf_url
        if "/_/downloads/" in p.path.lower():
            return False  # any explicit RTD download build (pdf, htmlzip, epub, …)
        return p.netloc.lower().endswith(".readthedocs.io")

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        p = urlparse(url)
        host = p.netloc.lower()
        slug = host.split(".")[0]
        lang, version = _lang_version(p.path)
        dl = f"{p.scheme}://{host}/_/downloads/{lang}/{version}/pdf/"
        try:
            _final, data = http.fetch_bytes(dl)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise  # fetch failed — orchestrate reports it honestly (exit 4)
            # No PDF build published — crawl the rendered docs instead.
            log.info("readthedocs.no_pdf_build", download=dl, status=exc.code)
            base = f"{p.scheme}://{host}/{lang}/{version}/"
            return _sphinx.acquire(base, workdir, slug=slug, title=None)
        if not data.startswith(b"%PDF"):
            raise InvalidInputError(f"{dl} did not serve a PDF — unexpected RTD response")
        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{slug}.pdf").write_bytes(data)
        log.info("readthedocs.acquire", url=url, download=dl, slug=slug, bytes=len(data))
        return AcquireResult(raw_dir=raw_dir, kind="pdf", slug=slug, pages=1)

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        if acq.kind == "pdf":
            # Passthrough: the downloaded PDF is already a pagespeak input.
            return next(acq.raw_dir.glob("*.pdf"))
        # Sphinx-crawl fallback: same merge shape as docs_probe's html branch.
        return DocsProbePattern().normalize(acq, workdir)
