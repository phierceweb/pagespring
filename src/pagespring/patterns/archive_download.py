"""archive_download — documentation shipped as a downloadable archive.

acquire: download a ``.zip`` / ``.tar.*`` / ``.epub`` and extract it. normalize:
concatenate the extracted text/markdown files (sorted) into one file, or, for an
HTML archive, the HTML pages. Covers Python's docs archives
(``python-3.x-docs-text.zip`` — clean plain text) and the Read-the-Docs /
Sphinx ecosystem.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from pf_core.log import get_logger
from pf_core.utils.slugify import slugify

from pagespring import http
from pagespring.base import AcquireResult, SourceKind

log = get_logger(__name__)

_ARCHIVE_SUFFIXES = (".zip", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".epub")
_TEXTY = (".txt", ".md", ".rst")
_HTMLY = (".html", ".htm")


def _slug_from(url: str) -> str:
    name = Path(urlparse(url).path).name
    for suf in _ARCHIVE_SUFFIXES:
        if name.lower().endswith(suf):
            name = name[: -len(suf)]
            break
    return slugify(name) or "docs"


def _extract(data: bytes, dest: Path) -> None:
    # Sources here are trusted docs archives (python.org, Read the Docs).
    bio = io.BytesIO(data)
    if zipfile.is_zipfile(bio):
        bio.seek(0)
        with zipfile.ZipFile(bio) as z:
            z.extractall(dest)
    else:
        bio.seek(0)
        with tarfile.open(fileobj=bio, mode="r:*") as t:
            t.extractall(dest, filter="data")


class ArchiveDownloadPattern:
    name = "archive_download"
    single_fetch = (
        True  # deliverable derives from exactly the one URL — refresh may probe validators
    )
    convert_recipe = ["--split-sections"]

    def match(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(path.endswith(s) for s in _ARCHIVE_SUFFIXES)

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        _f, data, meta = http.fetch_bytes_meta(url)
        _extract(data, raw_dir)
        kind: SourceKind = (
            "html" if any(raw_dir.rglob("*.html")) or any(raw_dir.rglob("*.htm")) else "markdown"
        )
        slug = _slug_from(url)
        exts = _HTMLY if kind == "html" else _TEXTY
        pages = sum(1 for p in raw_dir.rglob("*") if p.suffix.lower() in exts)
        log.info("archive_download.acquire", url=url, slug=slug, kind=kind, bytes=len(data))
        return AcquireResult(
            raw_dir=raw_dir,
            kind=kind,
            slug=slug,
            pages=pages,
            etag=meta["etag"],
            last_modified=meta["last_modified"],
        )

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        exts = _HTMLY if acq.kind == "html" else _TEXTY
        files = sorted(p for p in acq.raw_dir.rglob("*") if p.suffix.lower() in exts)
        parts = []
        for p in files:
            rel = p.relative_to(acq.raw_dir)
            parts.append(
                f"<!-- source: {rel} -->\n\n{p.read_text(encoding='utf-8', errors='replace')}"
            )
        suffix = "html" if acq.kind == "html" else "md"
        out = workdir / f"{acq.slug}.{suffix}"
        out.write_text("\n\n---\n\n".join(parts), encoding="utf-8")
        log.info("archive_download.normalize", slug=acq.slug, out=str(out), files=len(files))
        return out
