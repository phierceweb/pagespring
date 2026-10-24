"""Image localizer: ``pw_core.fetch.images`` downloads remote images into ``images/`` and re-points
refs; this module adds pagespring's naming, fetch policy and provenance sidecar."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from html import unescape
from html.entities import html5
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlparse

from pw_core.fetch import images as _core
from pw_core.pipeline.run_record import file_sha256
from pw_foundation.log import get_logger
from pw_foundation.utils.hashing import content_hash
from pw_foundation.utils.io import atomic_write_text

from pagespring import http

log = get_logger(__name__)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
# A numeric reference too long for any code point stays as written rather than decoding a prefix.
_CHAR_REF = re.compile(
    r"&(?:#\d{1,10}(?!\d);?|#[xX][0-9A-Fa-f]{1,8}(?![0-9A-Fa-f]);?|[A-Za-z][A-Za-z0-9]*;)"
)

SIDECAR_NAME = "images.json"
_SIDECAR_SCHEMA = 1


class _Provenance(TypedDict):
    """What the fetcher saw for one image, before it is joined to a local file."""

    source_url: str
    etag: str | None
    last_modified: str | None
    bytes: int


class ImageRecord(TypedDict):
    """One localized image's provenance — what a refresh needs to skip re-downloading."""

    local: str  # filename within images/
    source_url: str
    etag: str | None
    last_modified: str | None
    sha256: str
    bytes: int


class _PacedFetcher:
    """The localizer's transport: pagespring's fetch plus the inter-image delay, and the one place
    that sees an image's URL and bytes together, so provenance is captured here."""

    def __init__(self, taken: Iterable[str] = ()) -> None:
        # (sha256, fields, file): two URLs can yield identical bytes, so each fetch keeps its own.
        self.fetched: list[tuple[str, _Provenance, str | None]] = []
        self._taken = set(taken)

    def get_bytes(self, url: str, *, timeout_s: float | None = None) -> tuple[str, bytes]:
        """Fetch on ``fetch_bytes``' long budget, decoding the HTML-escaped ref: a CDN reads
        ``amp;wid`` as an unknown parameter and serves its default rendition."""
        decoded = decode_ref(url)
        final_url, data, meta = http.fetch_bytes_meta(decoded)
        fields: _Provenance = {
            "source_url": decoded,
            "etag": meta["etag"],
            "last_modified": meta["last_modified"],
            "bytes": len(data),
        }
        self.fetched.append((content_hash(data), fields, self._claim(url, data)))
        http.polite_sleep()
        return final_url, data

    def _claim(self, url: str, data: bytes) -> str | None:
        """The file the localizer saves these bytes as, claimed as it claims it: the proposed name
        (sniffed extension added when it has none), else the first free ``stem-N``."""
        sniffed = _core.sniff_image_ext(data)
        if sniffed is None:
            return None  # the localizer refuses a body that is not an image
        base = local_name(url)
        name = base if Path(base).suffix.lower() in _core._IMAGE_EXTENSIONS else base + sniffed
        stem, ext, n = Path(name).stem, Path(name).suffix, 2
        while name in self._taken:
            name, n = f"{stem}-{n}{ext}", n + 1
        self._taken.add(name)
        return name


def remote_image_urls(doc_path: Path) -> list[str]:
    """The remote image URLs in ``doc_path`` in first-seen order, by the localizer's own matcher, so
    it always agrees with ``count_remote_images``."""
    return [url for url, _fetch in _core._targets(doc_path.read_text(encoding="utf-8"), None)]


def read_sidecar(slug_dir: Path) -> list[ImageRecord]:
    """Per-image provenance for ``incoming/<slug>/``; empty when absent."""
    path = slug_dir / SIDECAR_NAME
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        log.warning("images.sidecar_unreadable", path=str(path))
        return []
    records: list[ImageRecord] = data.get("images", [])
    return records


def write_sidecar(slug_dir: Path, records: list[ImageRecord]) -> None:
    """Atomic: a torn sidecar orphans every image it failed to record."""
    slug_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        slug_dir / SIDECAR_NAME,
        json.dumps({"schema_version": _SIDECAR_SCHEMA, "images": records}, indent=2) + "\n",
        encoding="utf-8",
    )


def _merge(old: list[ImageRecord], new: list[ImageRecord]) -> list[ImageRecord]:
    """New records win per source_url; a resumed run must not drop earlier passes."""
    merged = {r["source_url"]: r for r in old}
    merged.update({r["source_url"]: r for r in new})
    return sorted(merged.values(), key=lambda r: r["source_url"])


def _decode_match(match: re.Match[str]) -> str:
    ref = match.group()
    # html.unescape prefix-matches an unknown name (``&copy2;`` -> ``©2;``); an attribute does not.
    return unescape(ref) if ref[1] == "#" or ref[1:] in html5 else ref


def decode_ref(url: str) -> str:
    """Decode an image ref's numeric references and its named ones that end in ``;``; any other
    ``&``, such as a bare ``&reg=`` or an unknown ``&copy2;``, stays as written."""
    return _CHAR_REF.sub(_decode_match, url)


def local_name(url: str) -> str:
    """Local name for a remote image ref: its decoded URL's sanitized, lowercased basename stem, its
    image extension when it has one (else the localizer sniffs one), and a digest of any query."""
    decoded = decode_ref(url)
    path = urlparse(decoded).path
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", Path(path).name)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-").lower() or "image"
    suffix = Path(path).suffix.lower()
    ext = ".jpg" if suffix == ".jpeg" else (suffix if suffix in _IMAGE_EXTS else "")
    return _core.with_query_digest(f"{stem}{ext}", decoded)


def count_remote_images(doc_path: Path) -> int:
    """Distinct remote image refs still in ``doc_path`` (0 ⇒ fully localized) — lets
    a caller know whether another ``download_images`` pass is needed."""
    return _core.count_remote_images(doc_path.read_text(encoding="utf-8"))


def download_images(doc_path: Path, images_dir: Path, *, checkpoint_every: int = 50) -> int:
    """Download remote images into ``images_dir``, re-pointing each ref as it lands so a killed run
    keeps its work; returns this run's count. Re-run until none remain."""
    # A file already here is not one of this run's downloads, whatever it hashes to, and the
    # localizer claims new names around it. Read from the directory, not the sidecar.
    preexisting = (
        {p.name for p in images_dir.glob("*") if p.is_file()} if images_dir.is_dir() else set()
    )
    fetcher = _PacedFetcher(taken=preexisting)
    try:
        return _core.localize_file(
            doc_path,
            images_dir,
            checkpoint_every=checkpoint_every,
            fetcher=fetcher,
            namer=local_name,
            reuse_existing=False,
        )
    finally:
        # An interrupted run keeps its checkpointed local refs, so it keeps their records too.
        if fetcher.fetched:
            _record_provenance(images_dir, fetcher.fetched)


def _record_provenance(
    images_dir: Path, fetched: list[tuple[str, _Provenance, str | None]]
) -> None:
    """Record each download with the file it was saved as, when that file holds its bytes: a refetch
    writes into the recorded file, so a guessed pairing would overwrite another ref's figure."""
    slug_dir = images_dir.parent
    records: list[ImageRecord] = []
    for digest, fields, local in fetched:
        if local is None:
            continue
        path = images_dir / local
        if not path.is_file() or file_sha256(path) != digest:
            continue
        records.append(
            {
                "local": local,
                "source_url": fields["source_url"],
                "etag": fields["etag"],
                "last_modified": fields["last_modified"],
                "sha256": digest,
                "bytes": fields["bytes"],
            }
        )
    write_sidecar(slug_dir, _merge(read_sidecar(slug_dir), records))
