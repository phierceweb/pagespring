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
from typing import NamedTuple, TypedDict, cast

from pf_core.exceptions import ClientError, InvalidInputError, PreconditionError
from pf_core.log import get_logger
from pf_core.utils.slugify import slugify

from pagespring import images as images_mod
from pagespring import manifest
from pagespring.base import AcquireResult, SourceKind
from pagespring.config import cfg
from pagespring.paths import slug_dir
from pagespring.registry import classify, pattern_by_name

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
    duplicate_of: str | None  # another slug already holding byte-identical content


def run_ingest(
    url: str,
    *,
    keep_raw: bool = False,
    download_images: bool = False,
    if_changed: bool = False,
    slug_override: str | None = None,
) -> IngestResult:
    """Acquire + normalize ``url`` into ``incoming/<slug>/`` and return stats.

    The result is one clean file (absolute asset URLs) under ``incoming/<slug>/``,
    plus a ``manifest.json`` recording its provenance and a content hash.
    With ``download_images``, an
    html/markdown source's remote images are pulled into ``incoming/<slug>/images/``
    and refs re-pointed there (PDF sources skip this). With ``keep_raw``, the raw
    crawl is kept alongside in ``raw/``.

    With ``if_changed``, a re-fetch that normalizes to byte-identical content
    leaves the existing deliverable untouched and returns ``changed=False`` (the
    crawl still runs — the slug is only known after acquire).

    ``slug_override`` renames the staged identity (dir, manifest, deliverable
    filename), folded via slugify.

    Returns a stats dict: pattern, slug, kind, clean (the incoming file), pages,
    bytes, images (count localized), changed, and duplicate_of (another slug
    already holding byte-identical content, or None).
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
            folded = slugify(slug_override)
            if not folded:
                raise InvalidInputError(f"--slug {slug_override!r} folds to an empty slug")
        else:
            folded = slugify(acq.slug)
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
                    "duplicate_of": duplicate_of,
                }

        # Re-ingest replaces: the slug dir holds exactly one ingest's output — no
        # orphaned clean files, no stale raw/. The image cache is the exception and
        # is carried across: a refresh brings the same image URLs back, so wiping
        # images/ and its sidecar would re-download every image every time.
        if incoming_dir.exists():
            _clear_except(
                incoming_dir,
                keep={"images", images_mod.SIDECAR_NAME, manifest.MANIFEST_NAME},
            )
        incoming_dir.mkdir(parents=True, exist_ok=True)
        # Stage as <slug>.<ext> regardless of what normalize called the file —
        # patterns that name output at acquire time can't see a --slug override.
        staged = incoming_dir / f"{acq.slug}{clean.suffix}"
        shutil.copy2(clean, staged)
        # A PDF's normalize is a passthrough, so a replay can only return the
        # bytes already staged — raw/ would duplicate the deliverable.
        if keep_raw and acq.kind == "pdf":
            log.info("ingest.raw_skipped", slug=acq.slug, reason="pdf normalize is a passthrough")
        elif keep_raw:
            shutil.copytree(acq.raw_dir, incoming_dir / "raw")

        record = manifest.build_manifest(
            source_url=url,
            pattern=pattern.name,
            slug=acq.slug,
            kind=acq.kind,
            deliverable=staged.name,
            pages=acq.pages,
            size_bytes=size_bytes,
            sha256=sha256,
            images=0,
            ingested_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            title=acq.title,
            etag=acq.etag,
            last_modified=acq.last_modified,
            truncated=acq.truncated,
            single_document=acq.single_document,
            # from the directory, not the flag — the manifest must not
            # promise a replay that isn't on disk.
            kept_raw=(incoming_dir / "raw").is_dir(),
            lost=acq.lost,
            localized_sha256=None,
        )
        # Before the image pass, so a run killed in there still leaves provenance.
        manifest.write_manifest(incoming_dir, record)

        n_images = 0
        if download_images and acq.kind in ("html", "markdown"):
            n_images = _image_pass(staged, incoming_dir).total
            record["images"] = n_images
            # The refs were re-pointed, so the staged sha no longer describes the
            # file on disk; without this the deliverable has no integrity record.
            record["localized_sha256"] = manifest.sha256_file(staged)
            manifest.write_manifest(incoming_dir, record)

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
            "duplicate_of": duplicate_of,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


class RenormalizeResult(TypedDict):
    """Stats from one renormalize replay (normalize re-run against kept raw/)."""

    pattern: str
    slug: str
    kind: str
    clean: str
    pages: int | None
    bytes: int
    changed: bool  # False when the replay normalized byte-identical to the staged deliverable


def run_renormalize(slug: str) -> RenormalizeResult:
    """Re-run the pattern's CURRENT normalize against ``incoming/<slug>/raw/``
    and re-stage the deliverable — no acquire, no network.

    Raw is copied to a fresh workdir so a mutating normalize can't corrupt the
    kept copy; the ``AcquireResult`` is rebuilt from the manifest.
    """
    incoming_dir = slug_dir(slug)
    m = manifest.read_manifest(incoming_dir)
    if m is None:
        raise PreconditionError(f"no manifest for {incoming_dir}/ — ingest it first")
    raw_src = incoming_dir / "raw"
    if not raw_src.is_dir():
        raise PreconditionError(
            f"no raw/ kept for {incoming_dir}/ — re-ingest with --keep-raw to enable renormalize"
        )
    pattern = pattern_by_name(m["pattern"])
    if pattern is None:
        raise PreconditionError(
            f"pattern '{m['pattern']}' (recorded in the manifest) is not registered — "
            "renamed or removed since the ingest?"
        )

    work = Path(mkdtemp(prefix="pagespring-"))
    try:
        raw_work = work / "raw"
        shutil.copytree(raw_src, raw_work)
        acq = AcquireResult(
            raw_dir=raw_work,
            kind=cast(SourceKind, m["kind"]),
            slug=m["slug"],
            pages=m["pages"],
            title=m.get("title"),  # absent in schema-v1 manifests → slug-fallback heading
        )
        clean = pattern.normalize(acq, work)
        if not clean.exists() or clean.stat().st_size == 0:
            raise EmptyOutputError(slug)

        sha256 = manifest.sha256_file(clean)
        size_bytes = clean.stat().st_size

        # Byte-identical replay: leave file, images, and mtime untouched — the
        # refactor-was-safe signal.
        if sha256 == m["sha256"]:
            log.info("renormalize.unchanged", pattern=pattern.name, slug=slug, sha256=sha256)
            return {
                "pattern": pattern.name,
                "slug": slug,
                "kind": m["kind"],
                "clean": str(incoming_dir / m["deliverable"]),
                "pages": m["pages"],
                "bytes": m["bytes"],
                "changed": False,
            }

        old = incoming_dir / m["deliverable"]
        staged = incoming_dir / f"{m['slug']}{clean.suffix}"  # same naming rule as ingest
        shutil.copy2(clean, staged)
        if old.exists() and old.name != staged.name:
            old.unlink()
        # Stale localized images would poison the next localize: its collision
        # set seeds from images/, forcing re-downloads onto suffixed names.
        shutil.rmtree(incoming_dir / "images", ignore_errors=True)

        m["deliverable"] = staged.name
        m["bytes"] = size_bytes
        m["sha256"] = sha256
        m["images"] = 0  # refs are absolute again; re-run localize to re-point them
        m["localized_sha256"] = None  # sha256 above describes the file on disk again
        manifest.write_manifest(incoming_dir, m)
        log.info("renormalize.done", pattern=pattern.name, slug=slug, clean=str(staged))
        return {
            "pattern": pattern.name,
            "slug": slug,
            "kind": m["kind"],
            "clean": str(staged),
            "pages": m["pages"],
            "bytes": size_bytes,
            "changed": True,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _clear_except(directory: Path, *, keep: set[str]) -> None:
    """Empty ``directory`` of everything not named in ``keep``."""
    for entry in directory.iterdir():
        if entry.name in keep:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


class _ImagePass(NamedTuple):
    """One image pass's counts: what it fetched, skipped, dropped, and left."""

    localized: int
    reused: int
    pruned: int
    remaining: int
    total: int  # images now in incoming/<slug>/images/


def _image_pass(deliverable: Path, incoming_dir: Path) -> _ImagePass:
    """Localize ``deliverable``'s remote images into ``incoming_dir/images/``.

    Manifest-free so ingest can run it before writing one. Ingest and localize
    share it: fetching without the reuse probe and the orphan sweep re-downloads
    every image onto a fresh ``-2``/``-3`` name on each re-ingest, stranding the
    previous run's files.
    """
    from pagespring import images

    images_dir = incoming_dir / "images"
    # Heal a cache written under the old mixed-case naming before anything reads it.
    images.normalize_case(deliverable, incoming_dir)
    # On a refresh the deliverable comes back with the same image URLs; anything the
    # sidecar holds and the server still calls unchanged is re-pointed, not re-fetched.
    reused = images.reuse_unchanged(deliverable, incoming_dir)
    localized = images.download_images(deliverable, images_dir)
    remaining = images.count_remote_images(deliverable)
    # Only safe once nothing is still remote — see prune_orphans.
    pruned = images.prune_orphans(deliverable, incoming_dir)
    total = sum(1 for p in images_dir.iterdir() if p.is_file()) if images_dir.exists() else 0
    return _ImagePass(localized, reused, pruned, remaining, total)


class LocalizeResult(TypedDict):
    """Stats from one localize pass over an already-staged deliverable."""

    slug: str
    localized: int  # images downloaded THIS run
    reused: int  # refs re-pointed from the sidecar without a download
    pruned: int  # image files deleted because the deliverable no longer references them
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
    incoming_dir = slug_dir(slug)
    m = manifest.read_manifest(incoming_dir)
    if m is None:
        raise PreconditionError(f"no manifest for {incoming_dir}/ — ingest it first")
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

    p = _image_pass(deliverable, incoming_dir)

    m["images"] = p.total
    m["localized_sha256"] = manifest.sha256_file(deliverable)
    manifest.write_manifest(incoming_dir, m)
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
