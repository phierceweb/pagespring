"""Staging helpers for `run_ingest` — deciding whether a slug dir may be cleared."""

from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from pf_core.utils.url_parse import canonical_url


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


def _clear_except(directory: Path, *, keep: set[str]) -> None:
    """Empty ``directory`` of everything not named in ``keep``."""
    for entry in directory.iterdir():
        if entry.name in keep:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
