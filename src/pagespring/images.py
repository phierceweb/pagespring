"""Image localizer: ``pf_core.fetch.images`` downloads remote images into ``images/`` and re-points
refs; this module adds pagespring's naming, fetch policy and provenance sidecar."""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlparse

from pf_core.fetch import images as _core
from pf_core.log import get_logger
from pf_core.pipeline.run_record import file_sha256
from pf_core.utils.hashing import content_hash
from pf_core.utils.io import atomic_write_text

from pagespring import http

log = get_logger(__name__)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

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

    def __init__(self) -> None:
        # Keyed by nothing: two URLs can yield identical bytes, and collapsing
        # them on hash would leave the second file untracked.
        self.fetched: list[tuple[str, _Provenance]] = []  # (sha256, fields)

    def get_bytes(self, url: str, *, timeout_s: float | None = None) -> tuple[str, bytes]:
        """Fetch on ``fetch_bytes``' long budget, unescaping the HTML-escaped ref: a CDN reads
        ``amp;wid`` as an unknown parameter and serves its default rendition."""
        url = unescape(url)
        final_url, data, meta = http.fetch_bytes_meta(url)
        self.fetched.append(
            (
                content_hash(data),
                {
                    "source_url": url,
                    "etag": meta["etag"],
                    "last_modified": meta["last_modified"],
                    "bytes": len(data),
                },
            )
        )
        http.polite_sleep()
        return final_url, data


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


def local_name(url: str) -> str:
    """Local name for a remote image: the sanitized, lowercased basename stem, plus the URL's image
    extension when it has one (else the localizer sniffs one)."""
    path = urlparse(url).path
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", Path(path).name)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-").lower() or "image"
    suffix = Path(path).suffix.lower()
    ext = ".jpg" if suffix == ".jpeg" else (suffix if suffix in _IMAGE_EXTS else "")
    return f"{stem}{ext}"


def count_remote_images(doc_path: Path) -> int:
    """Distinct remote image refs still in ``doc_path`` (0 ⇒ fully localized) — lets
    a caller know whether another ``download_images`` pass is needed."""
    return _core.count_remote_images(doc_path.read_text(encoding="utf-8"))


def download_images(doc_path: Path, images_dir: Path, *, checkpoint_every: int = 50) -> int:
    """Download remote images into ``images_dir``, re-pointing each ref as it lands so a killed run
    keeps its work; returns this run's count. Re-run until none remain."""
    fetcher = _PacedFetcher()
    # A file already here is not one of this run's downloads, whatever it hashes
    # to. Read from the directory, which cannot be unreadable as a sidecar can.
    preexisting = (
        {p.name for p in images_dir.glob("*") if p.is_file()} if images_dir.is_dir() else set()
    )
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
            _record_provenance(images_dir, fetcher.fetched, preexisting=preexisting)


def _record_provenance(
    images_dir: Path, fetched: list[tuple[str, _Provenance]], *, preexisting: set[str]
) -> None:
    """Join this run's downloads to the files they became by content hash (the localizer picks the
    final name); when URLs share a hash, each URL's proposed name breaks the tie."""
    slug_dir = images_dir.parent
    prior = read_sidecar(slug_dir)

    unclaimed = list(fetched)
    records: list[ImageRecord] = []
    for path in sorted(images_dir.glob("*")):
        if not path.is_file() or path.name in preexisting:
            continue
        digest = file_sha256(path)
        candidates = [i for i, (h, _f) in enumerate(unclaimed) if h == digest]
        if not candidates:
            continue  # from an earlier run, not one of this run's downloads
        if len(candidates) == 1:
            pick = candidates[0]
        else:
            # The hash cannot separate same-byte downloads, so the proposed name
            # breaks the tie; a sniffed extension matches none — leave it unrecorded.
            named = next(
                (i for i in candidates if local_name(unclaimed[i][1]["source_url"]) == path.name),
                None,
            )
            if named is None:
                continue
            pick = named
        _digest, fields = unclaimed.pop(pick)
        records.append(
            {
                "local": path.name,
                "source_url": fields["source_url"],
                "etag": fields["etag"],
                "last_modified": fields["last_modified"],
                "sha256": digest,
                "bytes": fields["bytes"],
            }
        )
    write_sidecar(slug_dir, _merge(prior, records))
