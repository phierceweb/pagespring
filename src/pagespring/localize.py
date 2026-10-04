"""Image localization over a staged deliverable: the pass `ingest --download-images`
runs after staging, and `localize` runs on its own to resume one."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple, TypedDict

from pf_core.exceptions import PreconditionError
from pf_core.log import get_logger

from pagespring import manifest
from pagespring._integrity import open_for_image_pass, read_usable
from pagespring.paths import slug_dir

log = get_logger(__name__)


class _ImagePass(NamedTuple):
    """One image pass's counts: what it fetched, skipped, dropped, and left."""

    localized: int
    reused: int
    pruned: int
    remaining: int
    total: int  # images now in incoming/<slug>/images/


def _image_pass(deliverable: Path, incoming_dir: Path) -> _ImagePass:
    """Localize ``deliverable``'s remote images. Ingest and localize share it: skipping the reuse
    probe and orphan sweep re-downloads every image onto a ``-2`` name on each re-ingest."""
    from pagespring import _image_cache, images

    images_dir = incoming_dir / "images"
    # Before anything reads the cache — see normalize_case.
    _image_cache.normalize_case(deliverable, incoming_dir)
    # On a refresh the deliverable comes back with the same image URLs; anything the
    # sidecar holds and the server still calls unchanged is re-pointed, not re-fetched.
    reused = _image_cache.reuse_unchanged(deliverable, incoming_dir)
    localized = images.download_images(deliverable, images_dir)
    remaining = images.count_remote_images(deliverable)
    # Only safe once nothing is still remote — see prune_orphans.
    pruned = _image_cache.prune_orphans(deliverable, incoming_dir)
    total = sum(1 for p in images_dir.iterdir() if p.is_file()) if images_dir.exists() else 0
    return _ImagePass(localized, reused, pruned, remaining, total)


def _recorded_image_pass(deliverable: Path, incoming_dir: Path, m: manifest.Manifest) -> _ImagePass:
    """Run ``_image_pass`` inside ``open_for_image_pass``, then record the image count and hash,
    even when cut short; a kill that skips this leaves the pass open for the next to resume."""
    open_for_image_pass(incoming_dir, m)
    finished = False
    try:
        passed = _image_pass(deliverable, incoming_dir)
        finished = True
        return passed
    finally:
        # The refs were re-pointed, so the staged sha no longer describes the file.
        digest = manifest.sha256_file(deliverable)
        images_dir = incoming_dir / "images"
        m["images"] = (
            sum(1 for f in images_dir.iterdir() if f.is_file()) if images_dir.is_dir() else 0
        )
        m["localized_sha256"] = digest if finished or digest != m["sha256"] else None
        m.pop("image_pass_open", None)
        manifest.write_manifest(incoming_dir, m)


class LocalizeResult(TypedDict):
    """Stats from one localize pass over an already-staged deliverable."""

    slug: str
    localized: int  # images downloaded THIS run
    reused: int  # refs re-pointed from the sidecar without a download
    pruned: int  # image files deleted because the deliverable no longer references them
    remaining: int  # remote refs still left (0 ⇒ fully localized)
    images_total: int  # images now in incoming/<slug>/images/


def localize_images(slug: str) -> LocalizeResult:
    """Download a staged deliverable's remote images into ``images/`` and re-point refs, without a
    re-crawl. Resumable: re-run until ``remaining`` is 0.

    Raises:
        PreconditionError: the slug has no readable manifest, or its deliverable is missing or
            no longer matches its record.
    """
    incoming_dir = slug_dir(slug)
    m = read_usable(incoming_dir)
    deliverable = incoming_dir / m["deliverable"]
    if not deliverable.exists():
        raise PreconditionError(f"deliverable missing: {deliverable}")

    # A PDF carries its images inline — no refs to re-point, and reading it as
    # text raises UnicodeDecodeError.
    if m["kind"] == "pdf":
        log.info("localize.skipped", slug=slug, reason="pdf carries its images inline")
        return {
            "slug": slug,
            "localized": 0,
            "reused": 0,
            "pruned": 0,
            "remaining": 0,
            "images_total": m["images"],
        }

    p = _recorded_image_pass(deliverable, incoming_dir, m)
    log.info(
        "localize.done",
        slug=slug,
        localized=p.localized,
        reused=p.reused,
        pruned=p.pruned,
        remaining=p.remaining,
        images=p.total,
    )
    return {
        "slug": slug,
        "localized": p.localized,
        "reused": p.reused,
        "pruned": p.pruned,
        "remaining": p.remaining,
        "images_total": p.total,
    }
