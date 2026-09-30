"""llms_txt — docs sites that publish an llms.txt index + per-page markdown.

Many modern docs platforms (Mintlify, GitBook, Anthropic's platform.claude.com)
expose ``/llms.txt`` listing every page, each with a per-page ``.md`` URL.
``acquire`` fetches the index, optionally filters to a section, and downloads
each page's markdown; ``normalize`` concatenates them in order. The output is
already clean markdown.

Point it at either the ``llms.txt`` URL directly (gets the whole site), or a
section base URL like ``https://platform.claude.com/docs/en/docs/claude-code``
(uses ``<host>/llms.txt`` and keeps only ``.md`` links under that prefix).
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns._gitbook import absolutize, strip_boilerplate
from pagespring.patterns._site import under_section

log = get_logger(__name__)

# Hosts known to publish an llms.txt (so a bare section URL still routes here).
_KNOWN_HOSTS = {
    "platform.claude.com",
    "docs.claude.com",
    "docs.anthropic.com",
    "code.claude.com",
}
_LLMS_NAMES = ("llms.txt", "llms-full.txt")
# .md URLs, whether bare (GitBook) or inside a markdown link (Mintlify/Anthropic).
_MD_URL_RE = re.compile(r"https?://[^\s)\]\"'<>]+\.md")
# Safety cap so a giant index can't trigger thousands of fetches by accident.
_MAX_PAGES = 1000
_SOURCE_RE = re.compile(r"\A<!-- source: (\S+) -->")


def _is_llms(url: str, *names: str) -> bool:
    """Whether the URL's path basename is one of ``names`` — a query string,
    fragment, or case change must not defeat the routing."""
    basename = PurePosixPath(urlparse(url).path.rstrip("/")).name.lower()
    return basename in names


def _clean_page(raw: str) -> str:
    """A saved page without its platform boilerplate, links absolute against its source.

    An ``llms-full.txt`` inlines many pages under one URL that is none of theirs, so
    only its root-relative links resolve."""
    md = strip_boilerplate(raw)
    source = _SOURCE_RE.match(md)
    if source is None:
        return md
    url = source.group(1)
    p = urlparse(url)
    page_url = None if _is_llms(url, "llms-full.txt") else url
    return absolutize(md, f"{p.scheme}://{p.netloc}", page_url)


def _llms_url_and_section(url: str) -> tuple[str, str | None]:
    """From the input URL derive (llms_txt_url, section_prefix | None)."""
    u = url.rstrip("/")
    if _is_llms(u, *_LLMS_NAMES):
        return u, None
    p = urlparse(u)
    # Prefix-filtering with a query or fragment attached matches no .md link.
    section = f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")
    return f"{p.scheme}://{p.netloc}/llms.txt", section


def _slug(url: str, section: str | None) -> str:
    p = urlparse(section or url)
    host = p.netloc.lower()
    parts = [s for s in p.path.split("/") if s and s.lower() not in _LLMS_NAMES]
    if section:
        return (parts[-1] if parts else host).replace(".", "-")
    # An llms file names nothing, so its bare path segment ("docs", "en") is the
    # same for every vendor; the host is what tells two of them apart.
    return "-".join([host, *parts]).replace(".", "-")


class LlmsTxtPattern:
    name = "llms_txt"

    def match(self, url: str) -> bool:
        if _is_llms(url, *_LLMS_NAMES):
            return True
        return urlparse(url).netloc.lower() in _KNOWN_HOSTS

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        llms_url, section = _llms_url_and_section(url)
        _final, index = http.fetch_text(llms_url)

        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        # llms-full.txt inlines the documentation rather than listing it, so the
        # body IS the deliverable; its .md citations are off-host noise.
        if _is_llms(llms_url, "llms-full.txt"):
            slug = _slug(url, section)
            (raw_dir / "0000-llms-full.md").write_text(
                f"<!-- source: {llms_url} -->\n\n{index}\n", encoding="utf-8"
            )
            log.info("llms_txt.acquire", llms=llms_url, full=True, pages=1, slug=slug)
            return AcquireResult(
                raw_dir=raw_dir, kind="markdown", slug=slug, pages=1, single_document=True
            )

        md_urls: list[str] = []
        seen: set[str] = set()
        for m in _MD_URL_RE.findall(index):
            # The .md must be in the PATH — a fragment ending in .md is an
            # in-page anchor to a page already listed, not a page of its own.
            if not urlparse(m).path.endswith(".md"):
                continue
            if section and not under_section(m, section):
                continue
            if m not in seen:
                seen.add(m)
                md_urls.append(m)

        truncated = len(md_urls) > _MAX_PAGES
        if truncated:
            log.warning("llms_txt.truncated", found=len(md_urls), cap=_MAX_PAGES)
            md_urls = md_urls[:_MAX_PAGES]

        saved = 0
        lost = 0
        for i, mu in enumerate(md_urls):
            try:
                _f, body = http.fetch_text(mu)
            except Exception as exc:
                lost += 1
                log.warning("llms_txt.fetch_error", url=mu, error=str(exc))
                continue
            stem = urlparse(mu).path.rstrip("/").rsplit("/", 1)[-1] or "page.md"
            if not stem.endswith(".md"):
                stem += ".md"  # normalize globs *.md; a miss here is silent content loss
            (raw_dir / f"{i:04d}-{stem}").write_text(
                f"<!-- source: {mu} -->\n\n{body}\n", encoding="utf-8"
            )
            saved += 1
            http.polite_sleep()

        slug = _slug(url, section)
        log.info(
            "llms_txt.acquire", llms=llms_url, section=section, pages=saved, slug=slug, lost=lost
        )
        return AcquireResult(
            raw_dir=raw_dir,
            kind="markdown",
            slug=slug,
            pages=saved,
            truncated=truncated,
            lost=lost,
        )

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        # The numeric filename prefix preserves llms.txt order under sort().
        parts = [
            _clean_page(p.read_text(encoding="utf-8")) for p in sorted(acq.raw_dir.glob("*.md"))
        ]
        out = workdir / f"{acq.slug}.md"
        out.write_text("\n\n---\n\n".join(parts), encoding="utf-8")
        log.info("llms_txt.normalize", slug=acq.slug, out=str(out), pages=len(parts))
        return out
