"""The docs_probe ladder: the strategy a URL's entry page names, found without crawling. ``detect``
is the live order, strongest evidence first; don't restate it here."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from pf_core.exceptions import ClientError, InvalidInputError
from pf_core.log import get_logger

from pagespring import http
from pagespring.patterns import (
    _antora,
    _clickhelp,
    _docsify,
    _flare,
    _fluidtopics,
    _gitbook,
    _hugo,
    _mdbook,
    _mediawiki,
    _paligo,
    _spec_ui,
    _sphinx,
    _st4,
    _starlight,
    _vitepress,
    _wordpress,
    _writerside,
)
from pagespring.patterns._site import (
    generator_meta,
    llms_index_candidates,
    llms_section,
    meta_refresh_target,
    page_title,
)
from pagespring.patterns._spec_ui import fetch_or_none as _fetch_or_none
from pagespring.patterns.api_spec import is_openapi

log = get_logger(__name__)
# Magic bytes survive the text decode (ASCII); a window allows leading whitespace/BOM.
_PDF_MAGIC = "%PDF-"
_MAGIC_WINDOW = 1024


def _is_mkdocs_index(body: str | None) -> bool:
    """A real MkDocs search index: a site answering 200 for any path makes existence meaningless, so
    the body must parse as one."""
    if body is None:
        return False
    try:
        return isinstance(json.loads(body).get("docs"), list)
    except (ValueError, AttributeError):
        return False


def _nearest_llms_index(base: str) -> tuple[str, list[str]] | None:
    """Directory and pages of the nearest ``llms.txt`` at or above ``base`` that lists pages."""
    for candidate in llms_index_candidates(base):
        http.polite_sleep()
        body = _fetch_or_none(candidate)
        # A soft-404 HTML page can carry .md links of its own; it is not an index.
        if body is None or body.lstrip().startswith("<"):
            continue
        pages = _gitbook.discover_pages(body)
        if pages:
            return candidate.rsplit("/", 1)[0], pages
    return None


# Generator tags naming a strategy, checked in this order.
_META_ROUTES = ("mkdocs", "docusaurus", "hugo", "asciidoctor")


@dataclass(frozen=True)
class Detection:
    """Where ``acquire`` hands a URL off: ``route`` names the hand-off (``pdf``, ``openapi``, a
    reference UI, a generator strategy, ``llms_txt``), ``via`` the evidence."""

    route: str
    via: str
    base: str
    final: str = ""
    home: str = ""
    title: str | None = None
    generator: str = ""
    index: str | None = None  # the llms.txt directory, for the llms_txt route
    section: str | None = None  # the part of that index the seed names
    oversize: ClientError | None = None  # the entry page outgrew the text budget
    page_url: str = ""  # what relative refs in ``home`` resolve against
    refreshed: bool = False  # the seed was a meta-refresh shell; ``final`` is its target


def _fetch_page(url: str) -> tuple[str, str] | ClientError:
    """``url``'s (final URL, text), or the error when the body outgrew the text budget; only a
    document download does, and pdf_url fetches that under the download budget."""
    try:
        return http.fetch_text(url)
    except ClientError as exc:
        if "max_bytes" not in exc.context:
            raise
        return exc


def detect(url: str) -> Detection:
    """Run the ladder against ``url`` up to its hand-off: the entry page, then the
    search-index and ``llms.txt`` probes when the page itself names nothing.

    Raises:
        InvalidInputError: no rung recognizes the site.
    """
    base = url.rstrip("/")
    fetched = _fetch_page(base)
    if isinstance(fetched, ClientError):
        return Detection("pdf", "oversize_body", base, oversize=fetched)
    final, home = fetched
    # The seed as typed when no redirect restored the slash its fetch dropped.
    page_url = url if final == base and url.endswith("/") else final
    # A same-site meta refresh is a shell (Writerside's entry page); the target is the page.
    target = meta_refresh_target(home, page_url)
    if target is not None:
        http.polite_sleep()
        fetched = _fetch_page(target)
        if isinstance(fetched, ClientError):
            return Detection("pdf", "oversize_body", target, oversize=fetched, refreshed=True)
        final, home = fetched
        base, page_url = final.rstrip("/"), final
    gen = generator_meta(home)

    def found(route: str, via: str, index: str | None = None) -> Detection:
        log.info("docs_probe.detected", generator=route, base=base, via=via)
        return Detection(
            route,
            via,
            base,
            final,
            home,
            page_title(home),
            gen,
            index=index,
            page_url=page_url,
            refreshed=target is not None,
        )

    # Content beats generator sniffing: a vendor may serve the manual itself
    # as a PDF from an extensionless path, which pdf_url.match cannot see.
    if _PDF_MAGIC in home[:_MAGIC_WINDOW]:
        return found("pdf", "magic_bytes")
    # A spec served from an extensionless path (springdoc's /v3/api-docs/<group>).
    if not home.lstrip().startswith("<") and is_openapi(home):
        return found("openapi", "content")
    # An API reference UI is a script shell; the spec it loads is the document.
    ui = _spec_ui.ui_name(home)
    if ui is not None:
        return found(ui, "page")

    # Before the meta sniff: ClickHelp publishes no generator meta at all, so
    # it is only identifiable by its own asset tells.
    if _clickhelp.is_clickhelp(home):
        return found("clickhelp", "asset_tells")
    # Also before the meta sniff: a Paligo *portal* shell carries no generator
    # meta (only its topic pages do), so probing the landing URL finds nothing.
    if _paligo.is_paligo(home):
        return found("paligo", "portal_tells")
    # Same two-faces problem: an ST4 entry page advertises only the
    # publisher's stylesheet, never "ST4" — the topic pages carry that.
    if _st4.is_st4(home):
        return found("st4", "entry_tells")
    if _writerside.is_writerside(home):
        return found("writerside", "help_app_hooks")
    # Flare writes no generator meta either; its runtime attributes sit on every page's <html>.
    if _flare.is_flare(home):
        return found("flare", "runtime_attrs")
    # A Fluid Topics portal serves one app shell for every URL; its API holds the content.
    if _fluidtopics.is_fluidtopics(home):
        return found("fluidtopics", "app_shell")
    # mdBook writes no generator meta; its page template carries a comment instead.
    if _mdbook.is_mdbook(home):
        return found("mdbook", "comment")

    for route in _META_ROUTES:
        if route in gen:
            return found(route, "meta")
    # A UI that drops the generator tag still renders the platform's own content container.
    if _hugo.is_docsy(home):
        return found("hugo", "docsy_tells")
    if _antora.is_antora(home):
        return found("antora", "meta" if "antora" in gen else "layout")
    if _starlight.is_starlight(home):
        return found("starlight", "meta" if "starlight" in gen else "layout")
    if _vitepress.is_vitepress(home):
        return found("vitepress", "meta" if "vitepress" in gen else "theme_script")
    if _wordpress.is_wordpress(home):
        return found("wordpress", "meta" if "wordpress" in gen else "rest_link")
    if _mediawiki.is_mediawiki(home):
        return found("mediawiki", "meta" if "mediawiki" in gen else "api_link")
    if _sphinx.is_sphinx(home):
        return found("sphinx", "tells")
    # A Docsify shell serves itself for unknown paths, which would fool the probes below.
    if _docsify.is_docsify(home):
        return found("docsify", "runtime_script")
    http.polite_sleep()  # the entry page was the last request
    if _is_mkdocs_index(_fetch_or_none(f"{base}/search/search_index.json")):
        return found("mkdocs", "search_index")
    nearest = _nearest_llms_index(base)
    if nearest is not None:
        index, pages = nearest
        detected = found("llms_txt", f"{index}/llms.txt", index=index)
        return replace(detected, section=llms_section(base, index, pages))
    raise InvalidInputError(
        f"unrecognized docs site: {base} — probed for an API reference UI "
        "(Swagger UI/Redoc/Scalar), the generator meta tag "
        "(MkDocs/Docusaurus/Hugo/Asciidoctor/Antora/Starlight/VitePress/WordPress/MediaWiki/"
        "Sphinx), the tells of ClickHelp, Paligo, SCHEMA ST4, Writerside, MadCap Flare, Fluid "
        "Topics, mdBook, Docsy and Docsify, _static/ assets (Sphinx), "
        "search/search_index.json (MkDocs), and llms.txt at and above the URL's path; "
        f"none matched{f' (generator tags: {gen})' if gen else ''}. The source needs its "
        "own pattern (see docs/architecture.md, 'Adding a new pattern')."
    )
