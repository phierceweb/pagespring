"""archive_download — documentation shipped as a downloadable archive.

acquire: download a ``.zip`` / ``.tar.*`` / ``.epub`` and extract it. normalize:
concatenate the extracted text/markdown files (sorted) into one file, or, for an
HTML archive, the HTML pages. Covers Python's docs archives
(``python-3.x-docs-text.zip`` — clean plain text) and the Read-the-Docs /
Sphinx ecosystem.
"""

from __future__ import annotations

import gzip
import html as _html
import io
import lzma
import re
import tarfile
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from bs4 import BeautifulSoup
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger
from pf_core.utils.slugify import slugify

from pagespring import http
from pagespring.base import AcquireResult, SourceKind

log = get_logger(__name__)

_ARCHIVE_SUFFIXES = (".zip", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".epub")
_TEXTY = (".txt", ".md", ".rst")
_PACKAGING = frozenset(
    {"readme", "license", "licence", "changelog", "contributing", "install", "notice", "authors"}
)
_HTMLY = (".html", ".htm")

# Extraction budget, checked against declared member sizes before anything is written.
_MAX_EXTRACT_BYTES = 2 * 1024 * 1024 * 1024
_MAX_MEMBERS = 100_000
_MAX_RATIO = 100
# Below this many extracted bytes a high ratio is ordinary repetitive text, not a bomb.
_RATIO_FLOOR_BYTES = 16 * 1024 * 1024
_ARCHIVE_ERRORS = (
    zipfile.BadZipFile,
    tarfile.TarError,
    EOFError,
    zlib.error,
    lzma.LZMAError,
    gzip.BadGzipFile,
    NotImplementedError,
)


def _html_exts(raw_dir: Path) -> tuple[str, ...]:
    """HTML member extensions for this archive. ``.xhtml`` counts only inside an
    EPUB container: elsewhere a stray one must not flip the kind sniff to html
    and filter the real .md/.txt docs out of the deliverable."""
    epub = any(raw_dir.rglob("*.opf")) or any(
        p.read_text(encoding="utf-8", errors="replace").strip() == "application/epub+zip"
        for p in raw_dir.rglob("mimetype")
        if p.is_file()
    )
    return (*_HTMLY, ".xhtml") if epub else _HTMLY


def _slug_from(url: str) -> str:
    name = Path(urlparse(url).path).name
    for suf in _ARCHIVE_SUFFIXES:
        if name.lower().endswith(suf):
            name = name[: -len(suf)]
            break
    return slugify(name) or "docs"


def _load_bytes(src: str) -> tuple[bytes, http.Validators]:
    """Archive bytes from an http(s) URL, a ``file://`` URL, or a local path."""
    if src.startswith(("http://", "https://")):
        _final, data, meta = http.fetch_bytes_meta(src)
        return data, meta
    path = Path(url2pathname(urlparse(src).path) if src.startswith("file://") else src)
    if not path.is_file():
        raise InvalidInputError(f"not a fetchable URL or existing file: {src}")
    return path.read_bytes(), http.Validators(etag=None, last_modified=None)


def _natural_key(path: Path) -> tuple[object, ...]:
    """Sort key where embedded digits compare numerically, so ch2 precedes ch10."""
    return tuple(
        int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(path))
    )


def _spine_order(raw_dir: Path) -> list[Path]:
    """Member paths in EPUB reading order, each resolved against the OPF's own
    directory, from the OPF spine ([] if absent).

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
    paths = (unquote(h.split("#", 1)[0]) for h in spine if h)
    return [opf.parent / p for p in paths if p]


def _ordered_members(raw_dir: Path, exts: tuple[str, ...]) -> list[Path]:
    """Archive members in reading order: EPUB spine first, then natural sort."""
    members = [p for p in raw_dir.rglob("*") if p.suffix.lower() in exts]
    spine = _spine_order(raw_dir)
    if not spine:
        return sorted(members, key=_natural_key)
    by_path = {p.resolve(): p for p in members}
    by_name = {p.name: p for p in members}
    ordered: list[Path] = []
    listed: set[Path] = set()
    for href in spine:
        # Full path first: two chapters can share a basename in different folders,
        # and the name alone collapses them onto whichever one the dict kept.
        member = by_path.get(href.resolve()) or by_name.get(href.name)
        if member is not None and member not in listed:
            listed.add(member)
            ordered.append(member)
    # Anything the spine omits still belongs in the deliverable, after the book.
    return ordered + sorted((p for p in members if p not in listed), key=_natural_key)


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


def _check_budget(sizes: Iterator[int], archive_bytes: int, src: str) -> None:
    """Refuse a bomb from its declared member sizes before any member reaches disk."""
    total = 0
    for members, size in enumerate(sizes, start=1):
        total += size
        if members > _MAX_MEMBERS:
            raise InvalidInputError(f"{src}: archive has more than {_MAX_MEMBERS} members")
        if total > _MAX_EXTRACT_BYTES:
            raise InvalidInputError(f"{src}: archive would extract past {_MAX_EXTRACT_BYTES} bytes")
        ratio = total / max(archive_bytes, 1)
        if total > _RATIO_FLOOR_BYTES and ratio > _MAX_RATIO:
            raise InvalidInputError(
                f"{src}: implausible compression ratio ({ratio:.0f}:1) for a docs archive"
            )


def _open_tar(data: bytes, src: str) -> tarfile.TarFile:
    try:
        return tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.ReadError as exc:
        got = " (got an HTML page)" if data.lstrip()[:1] == b"<" else ""
        raise InvalidInputError(f"{src}: not a zip, tar or epub archive{got}") from exc


def _extract(data: bytes, dest: Path, src: str) -> None:
    try:
        if zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                _check_budget((i.file_size for i in z.infolist()), len(data), src)
                z.extractall(dest)
            return
        with _open_tar(data, src) as tar:
            _check_budget((m.size for m in tar), len(data), src)
            tar.extractall(dest, filter="data")
    except _ARCHIVE_ERRORS as exc:
        raise InvalidInputError(f"{src}: damaged or unsafe archive: {exc}") from exc


class ArchiveDownloadPattern:
    name = "archive_download"
    single_fetch = True  # one-URL source; refresh may probe its stored validators

    def match(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(path.endswith(s) for s in _ARCHIVE_SUFFIXES)

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        data, meta = _load_bytes(url)
        _extract(data, raw_dir, url)
        htmly = _html_exts(raw_dir)
        members = [(p.suffix.lower(), p.stem.lower()) for p in raw_dir.rglob("*")]
        n_html = sum(1 for suffix, _ in members if suffix in htmly)
        n_text = sum(1 for suffix, _ in members if suffix in _TEXTY)
        # Which family carries the archive, not which is merely present: a text
        # archive shipping one search.html would otherwise filter out every doc,
        # and one README would outweigh the single page it describes.
        n_docs = sum(1 for suffix, stem in members if suffix in _TEXTY and stem not in _PACKAGING)
        kind: SourceKind = "html" if n_html > n_docs else "markdown"
        slug = _slug_from(url)
        pages = n_html if kind == "html" else n_text
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
        exts = _html_exts(acq.raw_dir) if acq.kind == "html" else _TEXTY
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
