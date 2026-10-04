"""archive_download: docs shipped as a ``.zip``, ``.tar.*`` or ``.epub``, extracted and joined into
one file, the text members or the HTML pages, an EPUB in spine order."""

from __future__ import annotations

import html as _html
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from bs4 import BeautifulSoup, Tag
from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger
from pf_core.utils.slugify import slugify

from pagespring import http
from pagespring.base import AcquireResult, SourceKind
from pagespring.patterns._archive_extract import extract
from pagespring.patterns._archive_images import MemberImages
from pagespring.patterns._archive_links import MemberLinks
from pagespring.patterns._ordering import natural_key

log = get_logger(__name__)

_ARCHIVE_SUFFIXES = (".zip", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".epub")
_TEXTY = (".txt", ".md", ".rst")
_PACKAGING = frozenset(
    {"readme", "license", "licence", "changelog", "contributing", "install", "notice", "authors"}
)
_HTMLY = (".html", ".htm")
_SEPARATOR = "\n\n---\n\n"
# Elements that are a member's content without any text.
_MEDIA = ["img", "image", "svg", "video", "audio", "object", "embed", "iframe", "math"]


def _html_exts(raw_dir: Path) -> tuple[str, ...]:
    """HTML member extensions: ``.xhtml`` counts only in an EPUB, since a stray one elsewhere would
    flip the kind sniff and filter the real docs out."""
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


class _Package(NamedTuple):
    """What an EPUB's OPF says about the book; empty for any other archive."""

    spine: list[Path]  # reading order, resolved against the OPF's own directory
    nav: set[Path]  # EPUB 3 navigation documents: the reading system's TOC
    title: str | None


def _package(raw_dir: Path) -> _Package:
    """The OPF's reading order, navigation documents and title; the spine is the only true order
    (filenames sort ch10 before ch2, and Gutenberg's cover ``wrap0000`` last)."""
    empty = _Package([], set(), None)
    opf = next(iter(sorted(raw_dir.rglob("*.opf"))), None)
    if opf is None:
        return empty
    try:
        root = ET.fromstring(opf.read_text(encoding="utf-8", errors="replace"))
    except ET.ParseError:
        return empty
    ns = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
    items = [
        i for i in root.iterfind(".//opf:manifest/opf:item", ns) if i.get("id") and i.get("href")
    ]
    hrefs = {i.get("id"): i.get("href") or "" for i in items}

    def resolve(href: str) -> Path | None:
        path = unquote(href.split("#", 1)[0])
        return opf.parent / path if path else None

    spine = [
        resolve(hrefs.get(r.get("idref") or "") or "")
        for r in root.iterfind(".//opf:spine/opf:itemref", ns)
    ]
    nav = [
        resolve(i.get("href") or "") for i in items if "nav" in (i.get("properties") or "").split()
    ]
    title = root.findtext(".//dc:title", default="", namespaces=ns).strip() or None
    return _Package([p for p in spine if p], {p.resolve() for p in nav if p}, title)


def _ordered_members(raw_dir: Path, exts: tuple[str, ...]) -> list[Path]:
    """Archive members in reading order: EPUB spine first, then natural sort."""
    members = [p for p in raw_dir.rglob("*") if p.suffix.lower() in exts]
    spine, nav, _title = _package(raw_dir)
    if not spine:
        return sorted(members, key=natural_key)
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
    # Anything else the spine omits still belongs in the deliverable, after the book.
    rest = (p for p in members if p not in listed and p.resolve() not in nav)
    return ordered + sorted(rest, key=natural_key)


def _content(member: Path) -> Tag | None:
    """A document's <body> less scripts and boilerplate, or None when nothing is left.
    Archives ship whole standalone pages; nesting them in one deliverable is invalid."""
    soup = BeautifulSoup(member.read_text(encoding="utf-8", errors="replace"), "html.parser")
    # .pg-boilerplate: Project Gutenberg's header and license footer.
    for junk in soup.find_all(["script", "style", "noscript"]) + soup.select(".pg-boilerplate"):
        junk.decompose()
    root = soup.body or soup
    return root if root.get_text(strip=True) or root.find(_MEDIA) else None


def _html_body(
    files: list[Path], raw_dir: Path, bundle: MemberImages, exts: tuple[str, ...]
) -> tuple[str, int]:
    """The members with content, joined, and how many there are."""
    links = MemberLinks(raw_dir, exts)
    parts = []
    for p in files:
        root = _content(p)
        if root is None:
            continue
        bundle.rewrite_html(root, p)
        links.include(root, p)
        parts.append(_part(p, raw_dir, root.decode_contents().strip()))
    return links.resolve(_SEPARATOR.join(parts)), len(parts)


def _text(member: Path, bundle: MemberImages) -> str:
    text = member.read_text(encoding="utf-8", errors="replace")
    suffix = member.suffix.lower()
    if suffix == ".md":
        return bundle.rewrite_markdown(text, member)
    return bundle.rewrite_rst(text, member) if suffix == ".rst" else text


def _part(member: Path, raw_dir: Path, text: str) -> str:
    return f"<!-- source: {member.relative_to(raw_dir)} -->\n\n{text}"


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
        extract(data, raw_dir, url)
        htmly = _html_exts(raw_dir)
        members = [(p.suffix.lower(), p.stem.lower()) for p in raw_dir.rglob("*")]
        n_html = sum(1 for suffix, _ in members if suffix in htmly)
        n_text = sum(1 for suffix, _ in members if suffix in _TEXTY)
        # Which family carries the archive, not which is present: one stray search.html must not
        # filter out a text archive, nor one README outweigh the single page it describes.
        n_docs = sum(1 for suffix, stem in members if suffix in _TEXTY and stem not in _PACKAGING)
        kind: SourceKind = "html" if n_html > n_docs else "markdown"
        slug = _slug_from(url)
        # normalize drops the HTML members left without content, and recounts.
        pages = len(_ordered_members(raw_dir, htmly)) if kind == "html" else n_text
        log.info("archive_download.acquire", url=url, slug=slug, kind=kind, bytes=len(data))
        return AcquireResult(
            raw_dir=raw_dir,
            kind=kind,
            slug=slug,
            pages=pages,
            title=_package(raw_dir).title,
            etag=meta["etag"],
            last_modified=meta["last_modified"],
        )

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        exts = _html_exts(acq.raw_dir) if acq.kind == "html" else _TEXTY
        files = _ordered_members(acq.raw_dir, exts)
        bundle = MemberImages(acq.raw_dir, workdir)
        if acq.kind == "html":
            body, acq.pages = _html_body(files, acq.raw_dir, bundle, exts)
        else:
            body = _SEPARATOR.join(_part(p, acq.raw_dir, _text(p, bundle)) for p in files)
        suffix = "html" if acq.kind == "html" else "md"
        out = workdir / f"{acq.slug}.{suffix}"
        if acq.kind == "html":
            title = _html.escape(acq.title or acq.slug.replace("-", " ").title())
            body = (
                '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
                f"<title>{title}</title></head>\n<body>\n{body}\n</body></html>\n"
            )
        out.write_text(body, encoding="utf-8")
        log.info(
            "archive_download.normalize",
            slug=acq.slug,
            out=str(out),
            files=len(files),
            images=bundle.copied,
        )
        return out
