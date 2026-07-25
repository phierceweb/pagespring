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

import re
from pathlib import Path
from urllib.parse import urlparse

from pf_core.fetch import images as _core

from pagespring import http

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


class _PacedFetcher:
    """The localizer's transport: pagespring's fetch plus the inter-image delay."""

    def get_bytes(self, url: str, *, timeout_s: float | None = None) -> tuple[str, bytes]:
        """Ignores the localizer's suggested timeout — an image download rides
        ``fetch_bytes``' long binary budget (tens of MB on slow CDNs)."""
        final_url, data = http.fetch_bytes(url)
        http.polite_sleep()
        return final_url, data


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
    """
    return _core.localize_file(
        doc_path,
        images_dir,
        checkpoint_every=checkpoint_every,
        fetcher=_PacedFetcher(),
        namer=_legacy_name,
        reuse_existing=False,
    )
