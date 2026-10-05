"""Renormalize: replay a pattern's current normalize against a slug's kept raw/."""

from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import mkdtemp
from typing import TypedDict, cast

from pf_core.exceptions import PreconditionError
from pf_core.log import get_logger

from pagespring import manifest
from pagespring._integrity import deliverable_intact, read_usable
from pagespring._staging import _stage_file, stage_bundled_images
from pagespring.base import AcquireResult, SourceKind
from pagespring.orchestrate import EmptyOutputError
from pagespring.paths import slug_dir
from pagespring.registry import pattern_by_name

log = get_logger(__name__)


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
    """Re-run the pattern's current normalize on ``incoming/<slug>/raw/`` and re-stage, offline; raw
    is copied first so a mutating normalize can't corrupt the kept copy."""
    incoming_dir = slug_dir(slug)
    m = read_usable(incoming_dir)
    raw_src = incoming_dir / "raw"
    if not raw_src.is_dir() or not m.get("kept_raw", True):
        raise PreconditionError(
            f"no complete raw/ kept for {incoming_dir}/ — re-ingest with --keep-raw "
            "to enable renormalize"
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
            lost=m.get("lost") or 0,
        )
        clean = pattern.normalize(acq, work)
        if not clean.exists() or clean.stat().st_size == 0:
            raise EmptyOutputError(slug)

        sha256 = manifest.sha256_file(clean)
        size_bytes = clean.stat().st_size

        # Byte-identical replay over an intact file: leave file, images, and mtime
        # untouched — the refactor-was-safe signal.
        if sha256 == m["sha256"] and deliverable_intact(incoming_dir, m):
            log.info("renormalize.unchanged", pattern=pattern.name, slug=slug, sha256=sha256)
            derived = (acq.pages, acq.lost, acq.spreads_split)
            if derived != (m["pages"], m.get("lost") or 0, m.get("spreads_split") or 0):
                m["pages"], m["lost"], m["spreads_split"] = derived
                manifest.write_manifest(incoming_dir, m)
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
        _stage_file(clean, staged)
        if old.exists() and old.name != staged.name:
            old.unlink()
        # A changed file drops images/: its names would push the next localize onto suffixed names.
        # A byte-identical replay names the very URLs the cache was fetched from.
        if sha256 != m["sha256"]:
            shutil.rmtree(incoming_dir / "images", ignore_errors=True)

        m["deliverable"] = staged.name
        m["bytes"] = size_bytes
        m["sha256"] = sha256
        m["images"] = 0  # refs are absolute again; re-run localize to re-point them
        m["localized_sha256"] = None  # sha256 above describes the file on disk again
        m.pop("image_pass_open", None)
        m["pages"], m["lost"], m["spreads_split"] = acq.pages, acq.lost, acq.spreads_split
        bundled = stage_bundled_images(clean, staged, incoming_dir)
        if bundled is not None:
            m["images"], m["localized_sha256"] = bundled, sha256
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
