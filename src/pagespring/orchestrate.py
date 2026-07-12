"""The ingest flow: classify a URL, then acquire + normalize ("fix") it via its
pattern into ``incoming/<slug>/`` — ONE clean file with absolute asset URLs.

That clean file is pagespring's deliverable. Converting it into the finished
``manuals/`` RAG corpus is a **separate** concern (pagespeak) that consumes
``incoming/`` on its own; pagespring neither runs nor knows about it.
"""

from __future__ import annotations

import shutil
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp
from typing import TypedDict

from pf_core.exceptions import PreconditionError
from pf_core.log import get_logger

from pagespring import manifest
from pagespring.config import cfg
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
    images: int
    changed: bool  # False only when --if-changed found the deliverable already current


def run_ingest(
    url: str,
    *,
    keep_raw: bool = False,
    download_images: bool = False,
    if_changed: bool = False,
) -> IngestResult:
    """Acquire + normalize ``url`` into ``incoming/<slug>/`` and return stats.

    The result is one clean file (absolute asset URLs) under ``incoming/<slug>/``,
    plus a ``manifest.json`` recording its provenance, the downstream
    ``convert_recipe``, and a content hash. With ``download_images``, an
    html/markdown source's remote images are pulled into ``incoming/<slug>/images/``
    and refs re-pointed there (PDF sources skip this). With ``keep_raw``, the raw
    crawl is kept alongside in ``raw/``.

    With ``if_changed``, a re-fetch that normalizes to byte-identical content
    leaves the existing deliverable untouched and returns ``changed=False`` (the
    crawl still runs — the slug is only known after acquire).

    Returns a stats dict: pattern, slug, kind, clean (the incoming file), pages,
    bytes, images (count localized), and changed.
    """
    pattern = classify(url)
    if pattern is None:
        raise NoPatternError(url)

    work = Path(mkdtemp(prefix="pagespring-"))
    try:
        try:
            acq = pattern.acquire(url, work)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise AcquireError(url, str(exc)) from exc
        clean = pattern.normalize(acq, work)
        if not clean.exists() or clean.stat().st_size == 0:
            raise EmptyOutputError(url)

        # Hash + size the normalized deliverable BEFORE staging/image-localization:
        # this is the content identity --if-changed compares against, and (on the
        # default no-image path) the on-disk file's own hash.
        sha256 = manifest.sha256_file(clean)
        size_bytes = clean.stat().st_size
        incoming_dir = Path(cfg.INCOMING_DIR) / acq.slug

        # --if-changed: an unchanged re-fetch preserves the existing deliverable,
        # its localized images, and its mtime — nothing is re-staged.
        if if_changed:
            prior = manifest.read_manifest(incoming_dir)
            if prior is not None and prior["sha256"] == sha256:
                log.info("ingest.unchanged", pattern=pattern.name, slug=acq.slug, sha256=sha256)
                return {
                    "pattern": pattern.name,
                    "slug": acq.slug,
                    "kind": acq.kind,
                    "clean": str(incoming_dir / prior["deliverable"]),
                    "pages": acq.pages,
                    "bytes": prior["bytes"],
                    "images": prior["images"],
                    "changed": False,
                }

        # Re-ingest replaces: the slug dir holds exactly one ingest's output —
        # no orphaned clean files, no stale raw/ or images/ merged from last time.
        if incoming_dir.exists():
            shutil.rmtree(incoming_dir)
        incoming_dir.mkdir(parents=True)
        staged = incoming_dir / clean.name
        shutil.copy2(clean, staged)
        if keep_raw:
            shutil.copytree(acq.raw_dir, incoming_dir / "raw")

        n_images = 0
        if download_images and acq.kind in ("html", "markdown"):
            from pagespring import images

            n_images = images.download_images(staged, incoming_dir / "images")

        manifest.write_manifest(
            incoming_dir,
            manifest.build_manifest(
                source_url=url,
                pattern=pattern.name,
                slug=acq.slug,
                kind=acq.kind,
                deliverable=staged.name,
                convert_recipe=list(pattern.convert_recipe),
                pages=acq.pages,
                size_bytes=size_bytes,
                sha256=sha256,
                images=n_images,
                ingested_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        )

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
            "changed": True,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


class LocalizeResult(TypedDict):
    """Stats from one localize pass over an already-staged deliverable."""

    slug: str
    localized: int  # images downloaded THIS run
    remaining: int  # remote refs still left (0 ⇒ fully localized)
    images_total: int  # images now in incoming/<slug>/images/


def localize_images(slug: str) -> LocalizeResult:
    """Download an already-staged deliverable's remote images into
    ``incoming/<slug>/images/`` and re-point its refs — no re-crawl.

    The acquire/normalize deliverable is self-contained with absolute image URLs by
    design, so image localization is a separate, **resumable** step: re-run until
    ``remaining`` is 0 (this is how a book whose image set exceeds a single run's
    time budget gets fully localized). Updates the manifest's image count.

    Raises ``PreconditionError`` if the slug was never ingested (no manifest) or its
    deliverable is missing.
    """
    incoming_dir = Path(cfg.INCOMING_DIR) / slug
    m = manifest.read_manifest(incoming_dir)
    if m is None:
        raise PreconditionError(f"no manifest for incoming/{slug}/ — ingest it first")
    deliverable = incoming_dir / m["deliverable"]
    if not deliverable.exists():
        raise PreconditionError(f"deliverable missing: {deliverable}")

    from pagespring import images

    images_dir = incoming_dir / "images"
    localized = images.download_images(deliverable, images_dir)
    remaining = images.count_remote_images(deliverable)
    total = sum(1 for p in images_dir.iterdir() if p.is_file()) if images_dir.exists() else 0

    m["images"] = total
    manifest.write_manifest(incoming_dir, m)
    log.info("localize.done", slug=slug, localized=localized, remaining=remaining, images=total)
    return {"slug": slug, "localized": localized, "remaining": remaining, "images_total": total}
