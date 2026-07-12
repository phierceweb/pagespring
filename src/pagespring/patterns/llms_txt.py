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
from pathlib import Path
from urllib.parse import urlparse

from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns._gitbook import strip_banner

log = get_logger(__name__)

# Hosts known to publish an llms.txt (so a bare section URL still routes here).
_KNOWN_HOSTS = {
    "platform.claude.com",
    "docs.claude.com",
    "docs.anthropic.com",
    "code.claude.com",
}
# .md URLs, whether bare (GitBook) or inside a markdown link (Mintlify/Anthropic).
_MD_URL_RE = re.compile(r"https?://[^\s)\]\"'<>]+\.md")
# Safety cap so a giant index (e.g. a 1600-page platform llms.txt) can't trigger
# thousands of fetches by accident.
_MAX_PAGES = 1000


def _llms_url_and_section(url: str) -> tuple[str, str | None]:
    """From the input URL derive (llms_txt_url, section_prefix | None)."""
    u = url.rstrip("/")
    if u.endswith("llms.txt") or u.endswith("llms-full.txt"):
        return u, None
    p = urlparse(u)
    return f"{p.scheme}://{p.netloc}/llms.txt", u  # section base -> prefix filter


def _slug(url: str, section: str | None) -> str:
    p = urlparse(section or url)
    parts = [s for s in p.path.split("/") if s and not s.endswith("llms.txt")]
    return (parts[-1] if parts else p.netloc).replace(".", "-")


class LlmsTxtPattern:
    name = "llms_txt"
    convert_recipe = ["--split-sections"]

    def match(self, url: str) -> bool:
        u = url.rstrip("/")
        if u.endswith("llms.txt") or u.endswith("llms-full.txt"):
            return True
        return urlparse(url).netloc.lower() in _KNOWN_HOSTS

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        llms_url, section = _llms_url_and_section(url)
        _final, index = http.fetch_text(llms_url)

        md_urls: list[str] = []
        seen: set[str] = set()
        for m in _MD_URL_RE.findall(index):
            if section and not m.startswith(section):
                continue
            if m not in seen:
                seen.add(m)
                md_urls.append(m)

        if len(md_urls) > _MAX_PAGES:
            log.warning("llms_txt.truncated", found=len(md_urls), cap=_MAX_PAGES)
            md_urls = md_urls[:_MAX_PAGES]

        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for i, mu in enumerate(md_urls):
            try:
                _f, body = http.fetch_text(mu)
            except Exception as exc:
                log.warning("llms_txt.fetch_error", url=mu, error=str(exc))
                continue
            stem = urlparse(mu).path.rstrip("/").rsplit("/", 1)[-1] or "page.md"
            (raw_dir / f"{i:04d}-{stem}").write_text(
                f"<!-- source: {mu} -->\n\n{body}\n", encoding="utf-8"
            )
            saved += 1
            http.polite_sleep()

        slug = _slug(url, section)
        log.info("llms_txt.acquire", llms=llms_url, section=section, pages=saved, slug=slug)
        return AcquireResult(raw_dir=raw_dir, kind="markdown", slug=slug, pages=saved)

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        # The numeric filename prefix preserves llms.txt order under sort().
        parts = [
            strip_banner(p.read_text(encoding="utf-8")) for p in sorted(acq.raw_dir.glob("*.md"))
        ]
        out = workdir / f"{acq.slug}.md"
        out.write_text("\n\n---\n\n".join(parts), encoding="utf-8")
        log.info("llms_txt.normalize", slug=acq.slug, out=str(out), pages=len(parts))
        return out
