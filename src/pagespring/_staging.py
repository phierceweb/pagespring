"""Staging helpers for `run_ingest` — deciding whether a slug dir may be cleared."""

from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from pf_core.exceptions import InvalidInputError
from pf_core.utils.io import atomic_write_bytes
from pf_core.utils.url_parse import canonical_url

from pagespring import manifest
from pagespring._integrity import deliverable_intact, usable
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
    except (AttributeError, OSError, TypeError, ValueError):  # a hand-edited manifest
        return None


def _same_source(held_url: str, url: str) -> bool:
    """Whether a slug dir's recorded source and an incoming URL name one manual.

    ``canonical_url`` returns "" for any non-http scheme, so local sources compare
    by resolved path: the same file typed ``./spec.json``, ``spec.json`` or
    ``file://`` is one manual.
    """
    held_canonical, url_canonical = canonical_url(held_url), canonical_url(url)
    if held_canonical and url_canonical:
        return held_canonical == url_canonical
    held_local, url_local = _local_path(held_url), _local_path(url)
    if held_local and url_local:
        return held_local == url_local
    return held_url == url


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
) -> bool:
    """Refuse to stage ``url`` over what ``incoming_dir`` holds; return whether
    staging takes the slug over from a different manual."""
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
