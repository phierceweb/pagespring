"""gitbook: ``*.gitbook.io`` sites, each page's ``.md`` with its images resolved from the rendered
page (see ``_gitbook``); custom domains arrive through ``docs_probe``'s llms.txt sniff."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns import _gitbook

log = get_logger(__name__)

_MAX_PAGES = 1000  # a capped crawl sets truncated


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

    def match(self, url: str) -> bool:
        return urlparse(url).netloc.lower().endswith(".gitbook.io")

    def acquire(
        self,
        url: str,
        workdir: Path,
        *,
        slug: str | None = None,
        title: str | None = None,
        rendered: bool = True,
        section: str | None = None,
    ) -> AcquireResult:
        base = url.rstrip("/")
        p = urlparse(base)
        origin = f"{p.scheme}://{p.netloc}"

        _f, llms = http.fetch_text(f"{base}/llms.txt")
        pages = _gitbook.discover_pages(llms, section)
        truncated = len(pages) > _MAX_PAGES
        if truncated:
            log.warning("gitbook.truncated", found=len(pages), cap=_MAX_PAGES)
            pages = pages[:_MAX_PAGES]

        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        lost = 0
        for i, page in enumerate(pages):
            try:
                _m, md = http.fetch_text(page)
                html = ""
                if rendered:
                    try:
                        _h, html = http.fetch_text(page[:-3])  # rendered page (drop ".md")
                    except Exception as exc:
                        # Not a lost page — the markdown carries the text. Only the
                        # image refs stay unresolved at their /files/<id> form.
                        log.warning("gitbook.render_fetch_error", url=page[:-3], error=str(exc))
                clean = _gitbook.process_page(md, html, origin, page_url=page)
            except Exception as exc:
                lost += 1
                log.warning("gitbook.fetch_error", url=page, error=str(exc))
                continue
            stem = urlparse(page).path.rstrip("/").rsplit("/", 1)[-1] or "page.md"
            if not stem.endswith(".md"):
                stem += ".md"  # normalize globs *.md; a miss here is silent content loss
            (raw_dir / f"{i:04d}-{stem}").write_text(
                f"<!-- source: {page} -->\n\n{clean}\n", encoding="utf-8"
            )
            saved += 1
            http.polite_sleep()

        # docs_probe already identified the site and derived these; _slug only knows
        # *.gitbook.io, so a custom domain folds to its generic host label.
        slug = slug or _slug(url)
        log.info("gitbook.acquire", base=base, section=section, pages=saved, slug=slug, lost=lost)
        return AcquireResult(
            raw_dir=raw_dir,
            kind="markdown",
            slug=slug,
            pages=saved,
            lost=lost,
            title=title,
            truncated=truncated,
        )

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        parts = [p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.md"))]
        out = workdir / f"{acq.slug}.md"
        out.write_text("\n\n---\n\n".join(parts), encoding="utf-8")
        log.info("gitbook.normalize", slug=acq.slug, out=str(out), pages=len(parts))
        return out
