"""docs_probe — content-probing last resort for generator-built docs sites.

Generator-built docs sites carry no URL tell on custom domains, so ``match``
cannot route them. This pattern registers LAST, claims any http(s) URL the
specific patterns declined, and probes the base page at acquire time (the
api_spec precedent — cheap match, content sniff in acquire).

The ladder runs strongest evidence first: content type, then the asset tells of
tools that emit no generator tag, then ``<meta name="generator">``, then the
weaker fallback tells. ``acquire`` is the live list — don't restate it here.
Unrecognized sites raise ``InvalidInputError`` (exit 2) naming what was probed.

``classify`` reporting ``docs_probe`` therefore means "will content-probe at
acquire", not a confirmed source type.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import urlparse

from pf_core.exceptions import ClientError, InvalidInputError
from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns import (
    _asciidoctor,
    _clickhelp,
    _docusaurus,
    _gitbook,
    _hugo,
    _mkdocs,
    _paligo,
    _spec_ui,
    _sphinx,
    _st4,
    _wordpress,
)
from pagespring.patterns._site import (
    generator_meta,
    llms_index_candidates,
    page_title,
    slug_from_host,
)
from pagespring.patterns._spec_ui import fetch_or_none as _fetch_or_none
from pagespring.patterns.api_spec import ApiSpecPattern, is_openapi
from pagespring.patterns.gitbook import GitBookPattern
from pagespring.patterns.pdf_url import PdfUrlPattern

log = get_logger(__name__)

# Magic bytes survive the text decode (ASCII); a window allows leading whitespace/BOM.
_PDF_MAGIC = "%PDF-"
_MAGIC_WINDOW = 1024


def _is_mkdocs_index(body: str | None) -> bool:
    """A real MkDocs search index, not just a URL that answered.

    A site that serves 200 for unknown paths makes "the file exists" meaningless,
    so the body must parse as a search index.
    """
    if body is None:
        return False
    try:
        return isinstance(json.loads(body).get("docs"), list)
    except (ValueError, AttributeError):
        return False


def _nearest_llms_base(base: str) -> str | None:
    """Directory of the nearest ``llms.txt`` at or above ``base`` that lists pages."""
    for candidate in llms_index_candidates(base):
        http.polite_sleep()
        body = _fetch_or_none(candidate)
        # A soft-404 HTML page can carry .md links of its own; it is not an index.
        if body is not None and not body.lstrip().startswith("<") and _gitbook.discover_pages(body):
            return candidate.rsplit("/", 1)[0]
    return None


class DocsProbePattern:
    name = "docs_probe"

    def match(self, url: str) -> bool:
        # Last-resort claim on anything web-shaped the specific patterns declined.
        return urlparse(url).scheme in ("http", "https")

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        base = url.rstrip("/")
        p = urlparse(base)
        try:
            final, home = http.fetch_text(base)
        except ClientError as exc:
            # Only a document download outgrows the text budget; pdf_url fetches it
            # under the download budget and checks the magic bytes itself.
            if "max_bytes" not in exc.context:
                raise
            try:
                acq = PdfUrlPattern().acquire(base, workdir)
            except InvalidInputError:
                raise exc from None  # an oversize page, not a PDF: the budget is the news
            log.info("docs_probe.detected", generator="pdf", base=base, via="oversize_body")
            return acq
        slug = slug_from_host(p.netloc)
        title = page_title(home)

        # Content beats generator sniffing: a vendor may serve the manual itself
        # as a PDF from an extensionless path, which pdf_url.match cannot see.
        if _PDF_MAGIC in home[:_MAGIC_WINDOW]:
            log.info("docs_probe.detected", generator="pdf", base=base, via="magic_bytes")
            return PdfUrlPattern().acquire(base, workdir)
        # A spec served from an extensionless path (springdoc's /v3/api-docs/<group>).
        if not home.lstrip().startswith("<") and is_openapi(home):
            log.info("docs_probe.detected", generator="openapi", base=base, via="content")
            acq = ApiSpecPattern().acquire(final, workdir, text=home)
            acq.single_document = True
            return acq

        # An API reference UI is a script shell; the spec it loads is the document.
        ui = _spec_ui.ui_name(home)
        if ui is not None:
            # Relative refs resolve against the seed as typed when no redirect restored its slash.
            page_url = url if final == base and url.endswith("/") else final
            spec_url, spec = _spec_ui.find_spec(page_url, home, ui, fetch=_fetch_or_none)
            log.info("docs_probe.detected", generator=ui, base=base, via="spec_url")
            acq = ApiSpecPattern().acquire(spec_url, workdir, text=spec)
            acq.single_document = True
            return acq

        # Before the meta sniff: ClickHelp publishes no generator meta at all, so
        # it is only identifiable by its own asset tells.
        if _clickhelp.is_clickhelp(home):
            log.info("docs_probe.detected", generator="clickhelp", base=base, via="asset_tells")
            return _clickhelp.acquire(
                base, workdir, slug=_clickhelp.slug_from_path(base), title=title
            )

        # Also before the meta sniff: a Paligo *portal* shell carries no generator
        # meta (only its topic pages do), so probing the landing URL finds nothing.
        if _paligo.is_paligo(home):
            log.info("docs_probe.detected", generator="paligo", base=base, via="portal_tells")
            return _paligo.acquire(base, workdir, slug=slug, title=title)

        # Same two-faces problem: an ST4 entry page advertises only the
        # publisher's stylesheet, never "ST4" — the topic pages carry that.
        if _st4.is_st4(home):
            log.info("docs_probe.detected", generator="st4", base=base, via="entry_tells")
            return _st4.acquire(base, workdir, slug=slug, title=title)

        gen = generator_meta(home)
        if "mkdocs" in gen:
            log.info("docs_probe.detected", generator="mkdocs", base=base, via="meta")
            return _mkdocs.acquire(base, workdir, slug=slug, title=title)
        if "docusaurus" in gen:
            log.info("docs_probe.detected", generator="docusaurus", base=base, via="meta")
            return _docusaurus.acquire(base, workdir, slug=slug, title=title)
        if "hugo" in gen:
            log.info("docs_probe.detected", generator="hugo", base=base, via="meta")
            return _hugo.acquire(base, workdir, slug=slug, title=title)
        if "asciidoctor" in gen:
            log.info("docs_probe.detected", generator="asciidoctor", base=base, via="meta")
            return _asciidoctor.acquire(base, workdir, slug=slug, title=title)
        if _wordpress.is_wordpress(home):
            log.info("docs_probe.detected", generator="wordpress", base=base, via="meta")
            return _wordpress.acquire(base, workdir, slug=slug, title=title)
        if _sphinx.is_sphinx(home):
            log.info("docs_probe.detected", generator="sphinx", base=base, via="tells")
            return _sphinx.acquire(base, workdir, slug=slug, title=title)
        http.polite_sleep()
        if _is_mkdocs_index(_fetch_or_none(f"{base}/search/search_index.json")):
            log.info("docs_probe.detected", generator="mkdocs", base=base, via="search_index")
            return _mkdocs.acquire(base, workdir, slug=slug, title=title)
        llms_base = _nearest_llms_base(base)
        if llms_base is not None:
            # Only a GitBook page needs its rendered twin, to resolve /files/<id> images.
            rendered = not gen or "gitbook" in gen
            log.info("docs_probe.detected", generator="llms_txt", base=base, via=llms_base)
            http.polite_sleep()  # GitBook re-fetches the index it was handed
            return GitBookPattern().acquire(
                llms_base, workdir, slug=slug, title=title, rendered=rendered
            )
        raise InvalidInputError(
            f"unrecognized docs site: {base} — probed for an API reference UI "
            "(Swagger UI/Redoc/Scalar), the generator meta tag "
            "(MkDocs/Docusaurus/Hugo/Asciidoctor/WordPress/Sphinx), ClickHelp + Paligo + SCHEMA ST4 tells, "
            "_static/ assets (Sphinx), search/search_index.json (MkDocs), and llms.txt at and above the URL's path; "
            "none matched. The source needs its own pattern "
            "(see docs/architecture.md, 'Adding a new pattern')."
        )

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
        log.info("docs_probe.normalize", slug=acq.slug, out=str(out), pages=len(fragments))
        return out
