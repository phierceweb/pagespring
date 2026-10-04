"""docs_probe: the last pattern, claiming any http(s) URL the others declined and probing the entry
page at acquire (``_detect.detect``); unrecognized sites exit 2 naming what was probed."""

from __future__ import annotations

import html
import shutil
from pathlib import Path
from types import ModuleType
from urllib.parse import urlparse

from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger

from pagespring import http
from pagespring.base import IMAGES_DIR, AcquireResult
from pagespring.patterns import (
    _antora,
    _asciidoctor,
    _clickhelp,
    _docsify,
    _docusaurus,
    _flare,
    _fluidtopics,
    _gitbook,
    _hugo,
    _mdbook,
    _mediawiki,
    _mkdocs,
    _paligo,
    _spec_ui,
    _sphinx,
    _st4,
    _starlight,
    _vitepress,
    _wordpress,
    _writerside,
)
from pagespring.patterns._detect import detect
from pagespring.patterns._site import (
    slug_from_host,
)
from pagespring.patterns._spec_ui import fetch_or_none as _fetch_or_none
from pagespring.patterns.api_spec import ApiSpecPattern
from pagespring.patterns.gitbook import GitBookPattern
from pagespring.patterns.pdf_url import PdfUrlPattern

log = get_logger(__name__)

_STRATEGIES: dict[str, ModuleType] = {
    "clickhelp": _clickhelp,
    "paligo": _paligo,
    "st4": _st4,
    "writerside": _writerside,
    "flare": _flare,
    "fluidtopics": _fluidtopics,
    "mdbook": _mdbook,
    "mkdocs": _mkdocs,
    "docusaurus": _docusaurus,
    "hugo": _hugo,
    "asciidoctor": _asciidoctor,
    "antora": _antora,
    "starlight": _starlight,
    "vitepress": _vitepress,
    "wordpress": _wordpress,
    "mediawiki": _mediawiki,
    "sphinx": _sphinx,
    "docsify": _docsify,
}


class DocsProbePattern:
    name = "docs_probe"

    def match(self, url: str) -> bool:
        # Last-resort claim on anything web-shaped the specific patterns declined.
        return urlparse(url).scheme in ("http", "https")

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        found = detect(url)
        if found.route == "openapi":
            acq = ApiSpecPattern().acquire(found.final, workdir, text=found.home)
            acq.single_document = True
            return acq
        if found.route in _spec_ui.UI_NAMES:
            spec_url, spec = _spec_ui.find_spec(
                found.page_url, found.home, found.route, fetch=_fetch_or_none
            )
            acq = ApiSpecPattern().acquire(spec_url, workdir, text=spec)
            acq.single_document = True
            return acq

        # Every hand-off below fetches first, straight after the probe's last request.
        http.polite_sleep()
        if found.route == "pdf":
            try:
                acq = PdfUrlPattern().acquire(found.base, workdir)
            except InvalidInputError:
                if found.oversize is None:
                    raise
                raise found.oversize from None  # an oversize page, not a PDF: the budget is the news
            if found.oversize is not None:
                log.info("docs_probe.detected", generator="pdf", base=found.base, via=found.via)
            if found.refreshed:
                # refresh probes the seed; these validators describe the shell's target.
                acq.etag = acq.last_modified = None
            return acq
        slug = slug_from_host(urlparse(found.base).netloc)
        if found.route == "llms_txt":
            # Only a GitBook page needs its rendered twin, to resolve /files/<id> images.
            rendered = not found.generator or "gitbook" in found.generator
            return GitBookPattern().acquire(
                found.index or found.base,
                workdir,
                slug=slug,
                title=found.title,
                rendered=rendered,
                section=found.section,
            )
        if found.route == "clickhelp":
            slug = _clickhelp.slug_from_path(found.base)
        # Looked up per call: a strategy's module-level acquire is its seam.
        result: AcquireResult = _STRATEGIES[found.route].acquire(
            found.base, workdir, slug=slug, title=found.title
        )
        return result

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        if acq.kind == "pdf":
            return PdfUrlPattern().normalize(acq, workdir)
        if any(acq.raw_dir.glob("spec.*")):
            return ApiSpecPattern().normalize(acq, workdir)
        if acq.kind == "markdown":
            parts = [
                _gitbook.strip_banner(f.read_text(encoding="utf-8"))
                for f in sorted(acq.raw_dir.glob("*.md"))
            ]
            out = workdir / f"{acq.slug}.md"
            out.write_text("\n\n---\n\n".join(parts), encoding="utf-8")
            log.info("docs_probe.normalize", slug=acq.slug, out=str(out), pages=len(parts))
            return out
        fragments = [f.read_text(encoding="utf-8") for f in sorted(acq.raw_dir.glob("*.html"))]
        out = workdir / f"{acq.slug}.html"
        if not fragments:
            # 0 bytes trips orchestrate's EmptyOutputError before staging — a
            # hollow shell must not clobber a prior good deliverable.
            out.write_text("", encoding="utf-8")
        else:
            title = html.escape(acq.title or acq.slug)
            out.write_text(
                "<!DOCTYPE html>\n"
                f'<html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>\n'
                "<body>\n" + "\n".join(fragments) + "\n</body>\n</html>\n",
                encoding="utf-8",
            )
        bundled = acq.raw_dir / IMAGES_DIR
        if bundled.is_dir():
            shutil.copytree(bundled, out.parent / IMAGES_DIR, dirs_exist_ok=True)
        log.info("docs_probe.normalize", slug=acq.slug, out=str(out), pages=len(fragments))
        return out
