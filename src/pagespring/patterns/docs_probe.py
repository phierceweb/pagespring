"""docs_probe — content-probing last resort for generator-built docs sites.

MkDocs, Docusaurus, Hugo, and Sphinx sites carry no URL tell on custom domains,
so ``match`` cannot route them. This pattern registers LAST, claims any http(s)
URL the specific patterns declined, and sniffs the generator at acquire time
(the api_spec precedent — cheap match, content sniff in acquire): the base
page's ``<meta name="generator">`` first, then fallback tells — ``_static/``
assets (Sphinx), a ``search/search_index.json`` (MkDocs), an ``llms.txt``
with per-page .md links (GitBook-style sites on custom domains, handled by
the gitbook machinery so image proxies still resolve). Unrecognized sites
raise ``InvalidInputError`` (exit 2) naming what was probed.

``classify`` reporting ``docs_probe`` therefore means "will content-probe at
acquire", not a confirmed source type.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import urlparse

from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns import (
    _clickhelp,
    _docusaurus,
    _gitbook,
    _hugo,
    _mkdocs,
    _paligo,
    _sphinx,
)
from pagespring.patterns._site import generator_meta, page_title, slug_from_host
from pagespring.patterns.gitbook import GitBookPattern
from pagespring.patterns.pdf_url import PdfUrlPattern

log = get_logger(__name__)

# Magic bytes survive the text decode (ASCII); a window allows leading whitespace/BOM.
_PDF_MAGIC = "%PDF-"
_MAGIC_WINDOW = 1024


def _fetch_or_none(url: str) -> str | None:
    try:
        _final, body = http.fetch_text(url)
    except Exception:
        return None
    return body


def _is_mkdocs_index(body: str | None) -> bool:
    """A real MkDocs search index, not just a URL that answered.

    Sites that serve 200 for unknown paths make "the file exists" meaningless —
    one returned HTML here and got routed to mkdocs, whose acquire then rejected
    it, masking the actual reason the source was unsupported.
    """
    if body is None:
        return False
    try:
        return isinstance(json.loads(body).get("docs"), list)
    except (ValueError, AttributeError):
        return False


class DocsProbePattern:
    name = "docs_probe"

    def match(self, url: str) -> bool:
        # Last-resort claim on anything web-shaped the specific patterns declined.
        return urlparse(url).scheme in ("http", "https")

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        base = url.rstrip("/")
        p = urlparse(base)
        origin = f"{p.scheme}://{p.netloc}"
        _final, home = http.fetch_text(base)
        slug = slug_from_host(p.netloc)
        title = page_title(home)

        # Content beats generator sniffing: a vendor may serve the manual itself
        # as a PDF from an extensionless path, which pdf_url.match cannot see.
        if _PDF_MAGIC in home[:_MAGIC_WINDOW]:
            log.info("docs_probe.detected", generator="pdf", base=base, via="magic_bytes")
            return PdfUrlPattern().acquire(base, workdir)

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
        if "sphinx" in gen or "_static/" in home:
            log.info("docs_probe.detected", generator="sphinx", base=base, via="meta/_static")
            return _sphinx.acquire(base, workdir, slug=slug, title=title)
        if _is_mkdocs_index(_fetch_or_none(f"{base}/search/search_index.json")):
            log.info("docs_probe.detected", generator="mkdocs", base=base, via="search_index")
            return _mkdocs.acquire(base, workdir, slug=slug, title=title)
        llms = _fetch_or_none(f"{origin}/llms.txt")
        if llms is not None and _gitbook.discover_pages(llms):
            log.info("docs_probe.detected", generator="llms_txt", base=base, via="llms.txt")
            return GitBookPattern().acquire(origin, workdir)
        raise InvalidInputError(
            f"unrecognized docs site: {base} — probed the generator meta tag "
            "(MkDocs/Docusaurus/Hugo/Sphinx), ClickHelp + Paligo tells, _static/ assets (Sphinx), "
            "search/search_index.json (MkDocs), and /llms.txt; none matched. "
            "The source needs its own pattern "
            "(see docs/architecture.md, 'Adding a new pattern')."
        )

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        if acq.kind == "pdf":
            return PdfUrlPattern().normalize(acq, workdir)
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
