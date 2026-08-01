"""archive_download — documentation shipped as a downloadable archive.

acquire: download a ``.zip`` / ``.tar.*`` / ``.epub`` and extract it. normalize:
concatenate the extracted text/markdown files (sorted) into one file, or, for an
HTML archive, the HTML pages. Covers Python's docs archives
(``python-3.x-docs-text.zip`` — clean plain text) and the Read-the-Docs /
Sphinx ecosystem.
"""

from __future__ import annotations

import html as _html
import io
import re
import tarfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
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


def _natural_key(path: Path) -> tuple[object, ...]:
    """Sort key where embedded digits compare numerically, so ch2 precedes ch10."""
    return tuple(
        int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(path))
    )


def _spine_order(raw_dir: Path) -> list[str]:
    """Member hrefs in EPUB reading order, from the OPF spine ([] if absent).

    The spine is the only authoritative order: filenames sort ch10 between ch1
    and ch2, and Gutenberg names its cover ``wrap0000`` so it lands last.
    """
    opf = next(iter(sorted(raw_dir.rglob("*.opf"))), None)
    if opf is None:
        return []
    try:
        root = ET.fromstring(opf.read_text(encoding="utf-8", errors="replace"))
    except ET.ParseError:
        return []
    ns = {"opf": "http://www.idpf.org/2007/opf"}
    hrefs = {
        item.get("id"): item.get("href")
        for item in root.iterfind(".//opf:manifest/opf:item", ns)
        if item.get("id") and item.get("href")
    }
    spine = [
        hrefs.get(ref.get("idref") or "") for ref in root.iterfind(".//opf:spine/opf:itemref", ns)
    ]
    return [h for h in spine if h]


def _ordered_members(raw_dir: Path, exts: tuple[str, ...]) -> list[Path]:
    """Archive members in reading order: EPUB spine first, then natural sort."""
    members = [p for p in raw_dir.rglob("*") if p.suffix.lower() in exts]
    spine = _spine_order(raw_dir)
    if not spine:
        return sorted(members, key=_natural_key)
    rank = {href: i for i, href in enumerate(spine)}
    by_name = {p.name: p for p in members}
    ordered = [by_name[Path(h).name] for h in spine if Path(h).name in by_name]
    listed = set(ordered)
    # Anything the spine omits still belongs in the deliverable, after the book.
    return ordered + sorted(
        (p for p in members if p not in listed),
        key=lambda p: (rank.get(p.name, len(rank)), _natural_key(p)),
    )


def _body_fragment(html: str) -> str:
    """A document's <body> inner HTML — archives ship whole standalone pages,
    and nesting 14 of them inside one deliverable is invalid markup."""
    soup = BeautifulSoup(html, "html.parser")
    for junk in soup.find_all(["script", "style", "noscript"]):
        junk.decompose()
    body = soup.body
    if body is None:
        return str(soup)
    return "".join(str(c) for c in body.contents).strip()


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
    single_fetch = True  # one-URL source; refresh may probe its stored validators

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
        files = _ordered_members(acq.raw_dir, exts)
        parts = []
        for p in files:
            rel = p.relative_to(acq.raw_dir)
            text = p.read_text(encoding="utf-8", errors="replace")
            if acq.kind == "html":
                text = _body_fragment(text)
            parts.append(f"<!-- source: {rel} -->\n\n{text}")
        suffix = "html" if acq.kind == "html" else "md"
        out = workdir / f"{acq.slug}.{suffix}"
        body = "\n\n---\n\n".join(parts)
        if acq.kind == "html":
            title = _html.escape(acq.title or acq.slug.replace("-", " ").title())
            body = (
                '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
                f"<title>{title}</title></head>\n<body>\n{body}\n</body></html>\n"
            )
        out.write_text(body, encoding="utf-8")
        log.info("archive_download.normalize", slug=acq.slug, out=str(out), files=len(files))
        return out
