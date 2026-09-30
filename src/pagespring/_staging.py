"""Staging helpers for `run_ingest` — deciding whether a slug dir may be cleared."""

from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from pf_core.exceptions import InvalidInputError
from pf_core.utils.io import atomic_write_bytes
from pf_core.utils.url_parse import canonical_url

from pagespring import _image_cache, images, manifest
from pagespring._integrity import deliverable_intact, usable
from pagespring.base import IMAGES_DIR
from pagespring.config import cfg


def _local_path(source: str) -> str | None:
    """``source`` resolved to a filesystem path, or None if it names no local file.

    None is the fail-safe answer: the caller then compares exactly, which never
    clears a directory it cannot prove holds the same manual.
    """
    try:
        parts = urlsplit(source)
        if parts.scheme and parts.scheme != "file":
            return None
        raw = url2pathname(parts.path) if parts.scheme else source
        return str(Path(raw).resolve())
    # RuntimeError: a symlink loop, on Python 3.12's Path.resolve.
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None


def source_key(source: str) -> str:
    """The identity every spelling of one manual's source shares.

    ``canonical_url`` returns "" for any non-http scheme, so local sources key
    by resolved path: the same file typed ``./spec.json``, ``spec.json`` or
    ``file://`` is one manual. Anything else keys as written.
    """
    return canonical_url(source) or _local_path(source) or source


def _same_source(held_url: str, url: str) -> bool:
    """Whether a slug dir's recorded source and an incoming URL name one manual."""
    return source_key(held_url) == source_key(url)


def _unchanged_record(incoming_dir: Path, url: str, sha256: str) -> manifest.Manifest | None:
    """The slug's record when it names this source, holds this content, and its
    deliverable is still the file it describes; otherwise None (re-stage)."""
    prior = manifest.read_manifest(incoming_dir)
    if not usable(prior) or not isinstance(prior["source_url"], str):
        return None
    if not _same_source(prior["source_url"], url) or prior["sha256"] != sha256:
        return None
    return prior if deliverable_intact(incoming_dir, prior) else None


def _guard_slug(
    incoming_dir: Path,
    url: str,
    *,
    replace: bool,
    slug_override: str | None,
    pages: int | None,
    truncated: bool,
    single_fetch: bool,
    protected: bool = False,
) -> bool:
    """Refuse to stage ``url`` over what ``incoming_dir`` holds; return whether
    staging takes the slug over from a different manual. A ``protected`` slug is
    never taken over."""
    if not incoming_dir.exists():
        return False
    slug = incoming_dir.name
    held = manifest.read_manifest(incoming_dir)
    held_url = held.get("source_url") if isinstance(held, dict) else None
    if held_url:
        takeover = not _same_source(held_url, url)
        holds = repr(held_url)
    else:
        # Nothing here can say what it holds, so content means refuse.
        takeover = any(incoming_dir.iterdir())
        holds = "an unidentified manual (no readable manifest)"
    if takeover and protected:
        raise InvalidInputError(
            f"slug {slug!r} holds {holds}, staged earlier in this batch — ingesting "
            f"{url!r} would delete it. Ingest this URL on its own with --slug to give "
            "it its own directory."
        )
    if takeover and not replace:
        own_dir = "--slug to give this source its own directory"
        escape = "a different --slug" if slug_override is not None else own_dir
        raise InvalidInputError(
            f"slug {slug!r} already holds {holds} — "
            f"ingesting {url!r} would delete it. Pass {escape}, or "
            "--replace to take the slug over."
        )
    if not takeover and not replace:
        _refuse_collapse(
            held,
            pages,
            slug=slug,
            truncated=truncated,
            single_fetch=single_fetch,
            holds_raw=(incoming_dir / "raw").is_dir(),
        )
    return takeover


def _refuse_collapse(
    held: object,
    pages: int | None,
    *,
    slug: str,
    truncated: bool = False,
    single_fetch: bool = False,
    holds_raw: bool = False,
) -> None:
    """Refuse a same-source re-crawl that found a fraction of the staged pages.

    A source that changed shape still normalizes to a non-empty shell, and staging
    it clears the manual it replaces. A crawl cut short by its page cap proves
    nothing about the source, so it never replaces a larger complete manual."""
    held_pages = held.get("pages") if isinstance(held, dict) else None
    if not isinstance(held_pages, int) or pages is None:
        return
    flags = "--replace --keep-raw" if holds_raw else "--replace"
    accept = f"Nothing was staged; re-ingest with --slug {slug} {flags} to accept it."
    held_truncated = isinstance(held, dict) and bool(held.get("truncated"))
    if truncated and pages < held_pages and not held_truncated:
        raise InvalidInputError(
            f"{slug}: the re-crawl stopped at its page cap with {pages} pages, but the "
            f"staged manual holds all {held_pages}. {accept}"
        )
    if held_pages < cfg.COLLAPSE_MIN_PAGES or pages * 100 >= held_pages * cfg.COLLAPSE_KEEP_PCT:
        return
    if single_fetch:
        raise InvalidInputError(
            f"{slug}: the re-fetched document counts {pages} where the staged one counts "
            f"{held_pages}, so the source may now serve a stub or a different document. "
            f"{accept}"
        )
    raise InvalidInputError(
        f"{slug}: the re-crawl found {pages} of the {held_pages} pages staged, so the "
        f"source likely changed shape. {accept}"
    )


def _stage_file(src: Path, dst: Path) -> None:
    """Put ``src``'s bytes at ``dst`` whole or not at all — a failed write leaves the
    previous ``dst`` in place."""
    atomic_write_bytes(dst, src.read_bytes())


def _clear_except(directory: Path, *, keep: set[str]) -> None:
    """Empty ``directory`` of everything not named in ``keep``."""
    for entry in directory.iterdir():
        if entry.name in keep:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


def stage_bundled_images(clean: Path, staged: Path, incoming_dir: Path) -> int | None:
    """Copy the files a pattern bundled beside ``clean`` into ``incoming_dir/images/``.

    Returns how many files images/ then holds, or None when nothing was bundled. A
    bundled file owns its name, so a localize record under it is dropped; files the
    staged deliverable no longer references are pruned (see ``prune_orphans``).
    """
    bundle = clean.parent / IMAGES_DIR
    if not bundle.is_dir():
        return None
    images_dir = incoming_dir / IMAGES_DIR
    images_dir.mkdir(parents=True, exist_ok=True)
    names = set()
    for src in sorted(bundle.iterdir()):
        if src.is_file():
            atomic_write_bytes(images_dir / src.name, src.read_bytes())
            names.add(src.name)
    records = images.read_sidecar(incoming_dir)
    kept = [r for r in records if r["local"] not in names]
    if len(kept) < len(records):
        images.write_sidecar(incoming_dir, kept)
    _image_cache.prune_orphans(staged, incoming_dir)
    return sum(1 for p in images_dir.iterdir() if p.is_file())
