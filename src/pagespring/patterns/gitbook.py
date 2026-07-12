"""gitbook — GitBook documentation sites.

GitBook serves an ``llms.txt`` index + a per-page ``.md`` variant, but its
markdown points images at internal ``/files/<id>`` paths that 404; the real
image lives behind a ``~gitbook/image`` proxy in the rendered HTML. acquire
fetches both per page and resolves the images (see _gitbook); normalize
concatenates.

Point it at a GitBook-hosted base URL, e.g. ``https://acme.gitbook.io/handbook``.
Custom-domain GitBook sites (e.g. ``https://docs.tableplus.com``) don't match
here directly — ``docs_probe`` sniffs their ``llms.txt`` and delegates back to
this pattern's ``acquire``.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns import _gitbook

log = get_logger(__name__)


def _slug(url: str) -> str:
    host = urlparse(url).netloc.lower()
    labels = host.split(".")
    if host.endswith("gitbook.io"):
        return labels[0]
    if len(labels) > 1 and labels[0] == "docs":
        return labels[1]
    return labels[0] if labels else "docs"


class GitBookPattern:
    name = "gitbook"

    # GitBook .md is already well-leveled; captions on by default; pagespeak
    # downloads the absolute image URLs and splits.
    convert_recipe = ["--split-sections"]

    def match(self, url: str) -> bool:
        # GitBook-hosted only. Custom domains (docs.<vendor>) are recognized by
        # docs_probe's llms.txt sniff and delegated back to this pattern's acquire.
        return urlparse(url).netloc.lower().endswith(".gitbook.io")

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        base = url.rstrip("/")
        p = urlparse(base)
        origin = f"{p.scheme}://{p.netloc}"

        _f, llms = http.fetch_text(f"{base}/llms.txt")
        pages = _gitbook.discover_pages(llms)

        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for i, page in enumerate(pages):
            try:
                _m, md = http.fetch_text(page)
                try:
                    _h, html = http.fetch_text(page[:-3])  # rendered page (drop ".md")
                except Exception:
                    html = ""  # text still converts; only images would be missed
                clean = _gitbook.process_page(md, html, origin)
            except Exception as exc:
                log.warning("gitbook.fetch_error", url=page, error=str(exc))
                continue
            stem = urlparse(page).path.rstrip("/").rsplit("/", 1)[-1] or "page.md"
            (raw_dir / f"{i:04d}-{stem}").write_text(
                f"<!-- source: {page} -->\n\n{clean}\n", encoding="utf-8"
            )
            saved += 1
            http.polite_sleep()

        slug = _slug(url)
        log.info("gitbook.acquire", base=base, pages=saved, slug=slug)
        return AcquireResult(raw_dir=raw_dir, kind="markdown", slug=slug, pages=saved)

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        parts = [p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.md"))]
        out = workdir / f"{acq.slug}.md"
        out.write_text("\n\n---\n\n".join(parts), encoding="utf-8")
        log.info("gitbook.normalize", slug=acq.slug, out=str(out), pages=len(parts))
        return out
