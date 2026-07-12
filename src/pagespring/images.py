"""Optional image localizer for HTML/markdown ingests.

Downloads a deliverable's remote images into a sibling ``images/`` dir and
re-points the refs at them — for a self-contained ``incoming/<slug>/``, and to
capture images behind expiring or tokened URLs (e.g. GitBook's
``?alt=media&token=…``) while they still resolve.

Opt-in via ``bin/run ingest --download-images`` or ``bin/run localize``.
Stdlib fetch only (``pagespring.http``).
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from pf_core.log import get_logger

from pagespring import http

log = get_logger(__name__)

# Markdown  ![alt](http…)  and HTML  <img … src="http…">.
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)")
_HTML_IMG_RE = re.compile(r"""<img\b[^>]*?\bsrc=["'](https?://[^"']+)["']""", re.IGNORECASE)

_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff"}
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"<svg", ".svg"),
    (b"<?xml", ".svg"),
)


def _ext_for(url: str, data: bytes) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in _IMG_EXTS:
        return ".jpg" if suffix == ".jpeg" else (".tiff" if suffix == ".tif" else suffix)
    head = data[:16]
    for magic, ext in _MAGIC:
        if head.startswith(magic):
            return ext
    if head[:4] == b"RIFF" and b"WEBP" in data[:16]:
        return ".webp"
    return ".img"


def _name_for(url: str, data: bytes, used: set[str]) -> str:
    base = Path(urlparse(url).path).name
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", base)  # drop ext; we set our own
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-") or "image"
    ext = _ext_for(url, data)
    name = f"{stem}{ext}"
    i = 2
    while name in used:
        name = f"{stem}-{i}{ext}"
        i += 1
    used.add(name)
    return name


def _remote_image_urls(text: str) -> list[str]:
    """Distinct remote (http/https) image refs in ``text``, in first-seen order."""
    urls: list[str] = []
    seen: set[str] = set()
    for rx in (_MD_IMG_RE, _HTML_IMG_RE):
        for u in rx.findall(text):
            if u not in seen:
                seen.add(u)
                urls.append(u)
    return urls


def count_remote_images(doc_path: Path) -> int:
    """Distinct remote image refs still in ``doc_path`` (0 ⇒ fully localized) — lets
    a caller know whether another ``download_images`` pass is needed."""
    return len(_remote_image_urls(doc_path.read_text(encoding="utf-8")))


def download_images(doc_path: Path, images_dir: Path, *, checkpoint_every: int = 50) -> int:
    """Download the doc's remote images into ``images_dir`` and re-point refs to
    ``images/<name>``. Returns the count downloaded this run; unfetchable refs are
    left untouched (logged).

    Resumable: each image is re-pointed in the deliverable the moment it lands (the
    file IS the progress ledger — finished refs are ``images/<name>``, pending ones
    stay remote), and the doc is checkpointed every ``checkpoint_every`` images, so
    a run killed partway keeps what it localized. ``used`` names are seeded from
    ``images_dir`` so a resumed run can't clobber a prior run's files. Re-run until
    ``count_remote_images`` returns 0 (how big books beat a per-run time cap).
    """
    text = doc_path.read_text(encoding="utf-8")
    urls = _remote_image_urls(text)
    if not urls:
        return 0

    images_dir.mkdir(parents=True, exist_ok=True)
    # Seed from a prior run's files so a resumed download can't clobber them.
    used: set[str] = {p.name for p in images_dir.iterdir() if p.is_file()}
    saved = 0
    since_checkpoint = 0
    # Longest URL first so one ref can't be a prefix of another when we re-point.
    for u in sorted(urls, key=len, reverse=True):
        try:
            _f, data = http.fetch_bytes(u)
        except Exception as exc:
            log.warning("images.fetch_error", url=u, error=str(exc))
            continue
        name = _name_for(u, data, used)
        (images_dir / name).write_bytes(data)
        text = text.replace(u, f"images/{name}")  # re-point now: the file is the ledger
        saved += 1
        since_checkpoint += 1
        if since_checkpoint >= checkpoint_every:
            doc_path.write_text(text, encoding="utf-8")
            since_checkpoint = 0
        http.polite_sleep()

    doc_path.write_text(text, encoding="utf-8")
    log.info("images.download", doc=str(doc_path), found=len(urls), saved=saved)
    return saved
