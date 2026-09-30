"""The ingest flow: classify a URL, then acquire + normalize ("fix") it via its
pattern into ``incoming/<slug>/`` — ONE clean file with absolute asset URLs.
"""

from __future__ import annotations

import shutil
import urllib.error
from collections.abc import Collection
from pathlib import Path
from tempfile import mkdtemp
from typing import TypedDict
from urllib.parse import urlsplit

from pf_core.exceptions import ClientError, InvalidInputError
from pf_core.log import get_logger
from pf_core.utils.dates import now_iso

from pagespring import images as images_mod
from pagespring import manifest
from pagespring._integrity import usable
from pagespring._staging import (
    _clear_except,
    _guard_slug,
    _stage_file,
    _unchanged_record,
    stage_bundled_images,
)
from pagespring.config import cfg
from pagespring.localize import _recorded_image_pass
from pagespring.paths import fold_slug
from pagespring.registry import classify

log = get_logger(__name__)


class NoPatternError(Exception):
    """No registered pattern matched the URL (the CLI turns this into guidance)."""


class EmptyOutputError(Exception):
    """normalize produced no content — the source likely changed shape. Raised
    BEFORE staging, so a previous good deliverable in incoming/<slug>/ survives."""


class AcquireError(Exception):
    """A network fetch died during acquire (the CLI shows this without a
    traceback). Carries the source URL and the underlying error text."""

    def __init__(self, url: str, detail: str):
        super().__init__(f"{detail} ({url})")
        self.url = url
        self.detail = detail


class IngestResult(TypedDict):
    """The stats dict run_ingest returns (one acquired+normalized manual)."""

    pattern: str
    slug: str
    kind: str
    clean: str
    pages: int | None
    bytes: int
    images: int  # files in incoming/<slug>/images/
    images_downloaded: int  # fetched by this run's image pass
    changed: bool  # False only when --if-changed found the deliverable already current
    duplicate_of: str | None  # another slug already holding byte-identical content


def run_ingest(
    url: str,
    *,
    keep_raw: bool = False,
    download_images: bool = False,
    if_changed: bool = False,
    slug_override: str | None = None,
    replace: bool = False,
    protected_slugs: Collection[str] = (),
) -> IngestResult:
    """Acquire + normalize ``url`` into ``incoming/<slug>/`` and return stats.

    The result is one clean file (absolute asset URLs) under ``incoming/<slug>/``,
    plus a ``manifest.json`` recording its provenance and a content hash. With
    ``download_images``, an html/markdown source's remote images are pulled into
    ``incoming/<slug>/images/`` and refs re-pointed there (PDF sources skip this).
    With ``keep_raw``, the raw crawl is kept alongside in ``raw/``.

    With ``if_changed``, a re-fetch that normalizes to byte-identical content
    leaves the existing deliverable untouched and returns ``changed=False`` (the
    crawl still runs — the slug is only known after acquire).

    ``slug_override`` renames the staged identity (dir, manifest, deliverable
    filename), folded via slugify. No other source takes over a slug in
    ``protected_slugs``, even with ``replace``: a batch's earlier lines staged them.
    """
    pattern = classify(url)
    if pattern is None:
        raise NoPatternError(url)

    work = Path(mkdtemp(prefix="pagespring-"))
    try:
        try:
            acq = pattern.acquire(url, work)
        # ClientError covers what the fetch core raises for a body it could not
        # trust: a malformed or truncated gzip, or one over the size cap.
        except (urllib.error.URLError, TimeoutError, ConnectionError, ClientError) as exc:
            raise AcquireError(url, str(exc)) from exc
        # Before normalize — patterns also use acq.slug in content (title fallback).
        # The pattern-derived slug is folded too, not just the override: it comes
        # from a remote URL, and `incoming/<slug>` is later cleared with rmtree,
        # so a slug of ".." would delete everything outside the corpus.
        if slug_override is not None:
            folded = fold_slug(slug_override)
            if not folded:
                raise InvalidInputError(f"--slug {slug_override!r} folds to an empty slug")
        else:
            folded = fold_slug(acq.slug)
            if not folded:
                raise InvalidInputError(
                    f"{pattern.name} derived an unusable slug {acq.slug!r} from {url!r}"
                )
        acq.slug = folded
        clean = pattern.normalize(acq, work)
        if not clean.exists() or clean.stat().st_size == 0:
            raise EmptyOutputError(url)

        # Hash + size the normalized deliverable BEFORE staging/image-localization:
        # this is the content identity --if-changed compares against, and (on the
        # default no-image path) the on-disk file's own hash.
        sha256 = manifest.sha256_file(clean)
        size_bytes = clean.stat().st_size
        incoming_dir = Path(cfg.INCOMING_DIR) / acq.slug
        duplicate_of = manifest.find_by_sha(Path(cfg.INCOMING_DIR), sha256, exclude_slug=acq.slug)
        if duplicate_of:
            log.warning("ingest.duplicate", slug=acq.slug, duplicate_of=duplicate_of)

        # --if-changed: an unchanged re-fetch preserves the existing deliverable,
        # its localized images, and its mtime — nothing is re-staged.
        prior = _unchanged_record(incoming_dir, url, sha256) if if_changed else None
        if prior is not None:
            log.info("ingest.unchanged", pattern=pattern.name, slug=acq.slug, sha256=sha256)
            # refresh probes with these; a source that re-stamped identical bytes
            # would never answer 304 to the ones it replaced. A record older than
            # the validator fields keeps its shape.
            fresh = (acq.etag, acq.last_modified)
            if "etag" in prior and (prior.get("etag"), prior.get("last_modified")) != fresh:
                prior["etag"], prior["last_modified"] = acq.etag, acq.last_modified
                manifest.write_manifest(incoming_dir, prior)
            return {
                "pattern": pattern.name,
                "slug": acq.slug,
                "kind": acq.kind,
                "clean": str(incoming_dir / prior["deliverable"]),
                "pages": acq.pages,
                "bytes": prior.get("bytes", size_bytes),
                "images": prior["images"],
                "images_downloaded": 0,
                "changed": False,
                "duplicate_of": duplicate_of,
            }

        # A slug whose record still describes its file keeps that record until the new
        # file lands; replaced first, a failed write would label the old bytes as new.
        held_record = (
            usable(manifest.read_manifest(incoming_dir)) if incoming_dir.exists() else False
        )
        takeover = _guard_slug(
            incoming_dir,
            url,
            replace=replace,
            slug_override=slug_override,
            pages=acq.pages,
            truncated=acq.truncated,
            single_fetch=getattr(pattern, "single_fetch", False),
            protected=acq.slug in protected_slugs,
        )
        incoming_dir.mkdir(parents=True, exist_ok=True)
        # Stage as <slug>.<ext> regardless of what normalize called the file —
        # patterns that name output at acquire time can't see a --slug override.
        staged = incoming_dir / f"{acq.slug}{clean.suffix}"
        # A bare local path names a different file from every other directory, and
        # refresh replays this string verbatim — persist the resolved one.
        recorded_url = url if urlsplit(url).scheme else str(Path(url).resolve())
        record = manifest.build_manifest(
            source_url=recorded_url,
            pattern=pattern.name,
            slug=acq.slug,
            kind=acq.kind,
            deliverable=staged.name,
            pages=acq.pages,
            size_bytes=size_bytes,
            sha256=sha256,
            images=0,
            ingested_at=now_iso(),
            title=acq.title,
            etag=acq.etag,
            last_modified=acq.last_modified,
            truncated=acq.truncated,
            single_document=acq.single_document,
            kept_raw=False,
            lost=acq.lost,
            localized_sha256=None,
        )
        # Otherwise before the copy, so a kill still leaves provenance — content with
        # no manifest reads as a foreign manual on the next ingest.
        if not held_record:
            manifest.write_manifest(incoming_dir, record)
        _stage_file(clean, staged)
        manifest.write_manifest(incoming_dir, record)
        # Only once the new deliverable is in place: the clear is unrecoverable.
        # A same-source re-ingest keeps its image cache — a refresh brings the same
        # image URLs back. A takeover's cache describes the displaced manual.
        keep = {manifest.MANIFEST_NAME, staged.name}
        if not takeover:
            keep |= {"images", images_mod.SIDECAR_NAME}
        _clear_except(incoming_dir, keep=keep)
        # A PDF's normalize is a passthrough, so a replay can only return the
        # bytes already staged — raw/ would duplicate the deliverable.
        if keep_raw and acq.kind == "pdf":
            log.info("ingest.raw_skipped", slug=acq.slug, reason="pdf normalize is a passthrough")
        elif keep_raw:
            shutil.copytree(acq.raw_dir, incoming_dir / "raw")
        bundled = stage_bundled_images(clean, staged, incoming_dir)
        if bundled is not None:
            # Local refs read as an image pass's work, which only localized_sha256 verifies.
            record["images"], record["localized_sha256"] = bundled, sha256
        # from the directory, not the flag — the manifest must not promise a
        # replay that isn't on disk.
        record["kept_raw"] = (incoming_dir / "raw").is_dir()
        manifest.write_manifest(incoming_dir, record)

        n_images, n_downloaded = record["images"], 0
        if download_images and acq.kind in ("html", "markdown"):
            passed = _recorded_image_pass(staged, incoming_dir, record)
            n_images, n_downloaded = passed.total, passed.localized

        log.info(
            "ingest.done",
            pattern=pattern.name,
            slug=acq.slug,
            clean=str(staged),
            pages=acq.pages,
            images=n_images,
        )
        return {
            "pattern": pattern.name,
            "slug": acq.slug,
            "kind": acq.kind,
            "clean": str(staged),
            "pages": acq.pages,
            "bytes": size_bytes,
            "images": n_images,
            "images_downloaded": n_downloaded,
            "changed": True,
            "duplicate_of": duplicate_of,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
