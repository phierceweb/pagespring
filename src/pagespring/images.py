"""Optional image localizer for HTML/markdown ingests.

Downloads a deliverable's remote images into a sibling ``images/`` dir and
re-points the refs at them — for a self-contained ``incoming/<slug>/``, and to
capture images behind expiring or tokened URLs (e.g. GitBook's
``?alt=media&token=…``) while they still resolve.

Opt-in via ``bin/run ingest --download-images`` or ``bin/run localize``.
``pf_core.fetch.images`` does the work; this module keeps pagespring's on-disk
naming and routes downloads through ``pagespring.http``, so they carry the crawl
User-Agent and the polite delay.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlparse

from pf_core.fetch import images as _core
from pf_core.log import get_logger

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
    """The localizer's transport: pagespring's fetch plus the inter-image delay.

    Also the only place that sees both an image's URL and its bytes, so it is
    where provenance is captured — ``localize`` rewrites the ref immediately
    after, and the remote URL is gone from the deliverable for good.
    """

    def __init__(self) -> None:
        # Keyed by nothing: two URLs can yield identical bytes, and collapsing
        # them on hash would leave the second file untracked.
        self.fetched: list[tuple[str, _Provenance]] = []  # (sha256, fields)

    def get_bytes(self, url: str, *, timeout_s: float | None = None) -> tuple[str, bytes]:
        """Ignores the localizer's suggested timeout — an image download rides
        ``fetch_bytes``' long binary budget (tens of MB on slow CDNs)."""
        final_url, data, meta = http.fetch_bytes_meta(url)
        self.fetched.append(
            (
                hashlib.sha256(data).hexdigest(),
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
    """The remote image URLs still in ``doc_path``, in first-seen order.

    Delegates to the localizer's own matcher so this can never disagree with
    ``count_remote_images`` (a test pins the two together).
    """
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
    slug_dir.mkdir(parents=True, exist_ok=True)
    (slug_dir / SIDECAR_NAME).write_text(
        json.dumps({"schema_version": _SIDECAR_SCHEMA, "images": records}, indent=2) + "\n",
        encoding="utf-8",
    )


def _merge(old: list[ImageRecord], new: list[ImageRecord]) -> list[ImageRecord]:
    """New records win per source_url; a resumed run must not drop earlier passes."""
    merged = {r["source_url"]: r for r in old}
    merged.update({r["source_url"]: r for r in new})
    return sorted(merged.values(), key=lambda r: r["source_url"])


def reuse_unchanged(doc_path: Path, slug_dir: Path) -> int:
    """Re-point refs whose image the sidecar holds and the server calls unchanged;
    returns how many downloads that avoided.

    Run before ``download_images`` on a refreshed deliverable. A URL absent from
    the sidecar is never probed — it has to be fetched anyway.

    A known URL the server reports as *changed* has its stale file deleted here so
    the fresh download claims the same name. Without that, the localizer's
    collision handling writes ``banner-2.png`` beside an orphaned ``banner.png``,
    and every later refresh adds another suffix.
    """
    records = {r["source_url"]: r for r in read_sidecar(slug_dir)}
    if not records:
        return 0
    images_dir = slug_dir / "images"
    text = doc_path.read_text(encoding="utf-8")
    reused = 0
    superseded: list[str] = []
    for url in remote_image_urls(doc_path):
        rec = records.get(url)
        if rec is None or not (images_dir / rec["local"]).is_file():
            continue
        if http.not_modified(url, etag=rec["etag"], last_modified=rec["last_modified"]):
            text = text.replace(url, f"images/{rec['local']}")
            reused += 1
        else:
            (images_dir / rec["local"]).unlink(missing_ok=True)
            superseded.append(url)
    if reused:
        doc_path.write_text(text, encoding="utf-8")
    if superseded:
        for url in superseded:
            records.pop(url, None)
        write_sidecar(slug_dir, sorted(records.values(), key=lambda r: r["source_url"]))
    if reused or superseded:
        log.info("images.reuse", slug=slug_dir.name, reused=reused, superseded=len(superseded))
    return reused


def prune_orphans(doc_path: Path, slug_dir: Path) -> int:
    """Delete images the deliverable no longer references, and their records.

    Refuses while any remote ref remains: mid-localize the refs have not been
    rewritten yet, so every local file would look unreferenced and the whole
    cache would be deleted. Referencing is decided by the *document*, not the
    sidecar — a same-URL replacement overwrites its record, leaving the stale
    file untracked.
    """
    images_dir = slug_dir / "images"
    if not images_dir.is_dir():
        return 0
    if count_remote_images(doc_path):
        log.info("images.prune_skipped", slug=slug_dir.name, reason="remote refs remain")
        return 0

    text = doc_path.read_text(encoding="utf-8")
    pruned = 0
    for path in sorted(images_dir.glob("*")):
        if not path.is_file() or f"images/{path.name}" in text:
            continue
        path.unlink(missing_ok=True)
        pruned += 1
    if pruned:
        kept = [r for r in read_sidecar(slug_dir) if (images_dir / r["local"]).is_file()]
        write_sidecar(slug_dir, kept)
        log.info("images.pruned", slug=slug_dir.name, pruned=pruned)
    return pruned


def _legacy_name(url: str) -> str:
    """Local name for a remote image: sanitized basename stem, plus the URL's
    image extension when it has one (else the localizer sniffs it from the bytes)."""
    path = urlparse(url).path
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", Path(path).name)  # drop ext; we set our own
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-") or "image"
    suffix = Path(path).suffix.lower()
    ext = ".jpg" if suffix == ".jpeg" else (suffix if suffix in _IMAGE_EXTS else "")
    return f"{stem}{ext}"


def count_remote_images(doc_path: Path) -> int:
    """Distinct remote image refs still in ``doc_path`` (0 ⇒ fully localized) — lets
    a caller know whether another ``download_images`` pass is needed."""
    return _core.count_remote_images(doc_path.read_text(encoding="utf-8"))


def download_images(doc_path: Path, images_dir: Path, *, checkpoint_every: int = 50) -> int:
    """Download the doc's remote images into ``images_dir`` and re-point refs to
    ``images/<name>``. Returns the count downloaded this run; unfetchable refs are
    left untouched (logged).

    Resumable: each image is re-pointed in the deliverable the moment it lands (the
    file IS the progress ledger — finished refs are ``images/<name>``, pending ones
    stay remote), and the doc is checkpointed every ``checkpoint_every`` images, so
    a run killed partway keeps what it localized. Names are claimed against what is
    already in ``images_dir`` so a resumed run can't clobber a prior run's files.
    Re-run until ``count_remote_images`` returns 0 (how big books beat a per-run
    time cap).

    Also records each image's provenance in ``<slug>/images.json`` — the
    deliverable's refs are rewritten to ``images/<name>``, so this is the only
    surviving record of where an image came from.
    """
    fetcher = _PacedFetcher()
    downloaded = _core.localize_file(
        doc_path,
        images_dir,
        checkpoint_every=checkpoint_every,
        fetcher=fetcher,
        namer=_legacy_name,
        reuse_existing=False,
    )
    if fetcher.fetched:
        _record_provenance(images_dir, fetcher.fetched)
    return downloaded


def _record_provenance(images_dir: Path, fetched: list[tuple[str, _Provenance]]) -> None:
    """Join this run's downloads to the files they became, then merge the sidecar.

    The localizer picks the final filename itself (sniffing an extension,
    suffixing a collision), so the name proposed here is not necessarily the one
    on disk — hence matching on content hash. When several URLs share a hash the
    hash cannot separate them, so the name each URL *proposes* breaks the tie,
    and each download is claimed at most once.
    """
    unclaimed = list(fetched)
    records: list[ImageRecord] = []
    for path in sorted(images_dir.glob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        candidates = [i for i, (h, _f) in enumerate(unclaimed) if h == digest]
        if not candidates:
            continue  # from an earlier run; its record is already in the sidecar
        pick = next(
            (i for i in candidates if _legacy_name(unclaimed[i][1]["source_url"]) == path.name),
            candidates[0],
        )
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
    slug_dir = images_dir.parent
    write_sidecar(slug_dir, _merge(read_sidecar(slug_dir), records))
