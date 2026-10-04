"""Upkeep of a slug's ``images/`` cache around a download pass: case-fold names, reuse what the
server still serves unchanged, prune what the deliverable no longer references."""

from __future__ import annotations

from html import unescape
from pathlib import Path

from pf_core.fetch import images as _core
from pf_core.log import get_logger
from pf_core.utils.hashing import content_hash
from pf_core.utils.io import atomic_write_bytes, atomic_write_text

from pagespring import http
from pagespring.images import (
    ImageRecord,
    count_remote_images,
    local_name,
    read_sidecar,
    remote_image_urls,
    write_sidecar,
)

log = get_logger(__name__)


def normalize_case(doc_path: Path, slug_dir: Path) -> int:
    """Lowercase mixed-case image filenames with their refs and records: on a case-insensitive disk
    a download keeps the old case, and ``prune_orphans`` then deletes a file still in use."""
    images_dir = slug_dir / "images"
    if not images_dir.is_dir():
        return 0
    stale = [p for p in sorted(images_dir.iterdir()) if p.is_file() and p.name != p.name.lower()]
    if not stale:
        return 0
    text = doc_path.read_text(encoding="utf-8")
    records = {r["local"]: r for r in read_sidecar(slug_dir)}
    for old in stale:
        target = images_dir / old.name.lower()
        if target.exists() and not target.samefile(old):
            continue  # a distinct file already owns the lowercase name
        old.rename(target)
        text = text.replace(f"images/{old.name}", f"images/{target.name}")
        rec = records.pop(old.name, None)
        if rec is not None:
            rec["local"] = target.name
            records[target.name] = rec
    atomic_write_text(doc_path, text, encoding="utf-8")
    write_sidecar(slug_dir, sorted(records.values(), key=lambda r: r["source_url"]))
    log.info("images.case_normalized", slug=slug_dir.name, renamed=len(stale))
    return len(stale)


def reuse_unchanged(doc_path: Path, slug_dir: Path) -> int:
    """Re-point refs at sidecar images the server still serves unchanged; returns how many settled.
    Otherwise refetch: new bytes replace the file in place, a failed fetch keeps file and ref."""
    records = {r["source_url"]: r for r in read_sidecar(slug_dir)}
    if not records:
        return 0
    images_dir = slug_dir / "images"
    text = doc_path.read_text(encoding="utf-8")
    reused = refreshed = 0
    for url in remote_image_urls(doc_path):
        # Probe the decoded URL the sidecar keys on (an `&amp;` ref never matches its validators);
        # the rewrite below must still match the escaped form the document holds.
        decoded = unescape(url)
        rec = records.get(decoded)
        if rec is None or not (images_dir / rec["local"]).is_file():
            continue
        unchanged = False
        if rec["etag"] or rec["last_modified"]:
            unchanged = http.not_modified(
                decoded, etag=rec["etag"], last_modified=rec["last_modified"]
            )
            http.polite_sleep()
        if not unchanged:
            cached_sha = rec["sha256"]
            if not _refetch_into(rec, images_dir, decoded):
                continue
            if rec["sha256"] != cached_sha:
                refreshed += 1
        # The localizer's anchored rewriter: CDN sizing variants make one URL a prefix of another,
        # so a bare replace corrupts the longer ref.
        text = _core._retarget(text, url, f"images/{rec['local']}")
        reused += 1
    if reused:
        atomic_write_text(doc_path, text, encoding="utf-8")
        write_sidecar(slug_dir, sorted(records.values(), key=lambda r: r["source_url"]))
        log.info("images.reuse", slug=slug_dir.name, reused=reused, refreshed=refreshed)
    return reused


def _refetch_into(rec: ImageRecord, images_dir: Path, url: str) -> bool:
    """Fetch ``url`` and, when its bytes still belong under ``rec["local"]``, write
    them there and update ``rec``; False leaves both untouched."""
    try:
        _final, data, meta = http.fetch_bytes_meta(url)
    except Exception as exc:
        log.warning("images.refetch_failed", url=url, error=str(exc))
        return False
    finally:
        http.polite_sleep()
    sniffed = _core.sniff_image_ext(data)
    name = local_name(url)
    suffix = Path(name).suffix.lower()
    # The localizer's own rule: only a known image extension is one.
    expected_ext = suffix if suffix in _core._IMAGE_EXTENSIONS else sniffed
    if sniffed is None or expected_ext != Path(rec["local"]).suffix:
        # Not an image (an expired token's login page), or a type that the
        # localizer would name differently; either way the cached copy stays.
        log.warning("images.refetch_kept", url=url, sniffed=sniffed, local=rec["local"])
        return False
    digest = content_hash(data)
    if digest != rec["sha256"]:
        atomic_write_bytes(images_dir / rec["local"], data)
        rec["sha256"], rec["bytes"] = digest, len(data)
    rec["etag"], rec["last_modified"] = meta["etag"], meta["last_modified"]
    return True


def prune_orphans(doc_path: Path, slug_dir: Path) -> int:
    """Delete images the document no longer references (the sidecar can miss a replaced file), and
    their records. Refuses while a ref is remote: mid-localize, every file looks unreferenced."""
    images_dir = slug_dir / "images"
    if not images_dir.is_dir():
        return 0
    if count_remote_images(doc_path):
        log.info("images.prune_skipped", slug=slug_dir.name, reason="remote refs remain")
        return 0

    text = doc_path.read_text(encoding="utf-8")
    pruned = 0
    for path in sorted(images_dir.glob("*")):
        if not path.is_file() or f"images/{path.name}" in text:
            continue
        path.unlink(missing_ok=True)
        pruned += 1
    if pruned:
        kept = [r for r in read_sidecar(slug_dir) if (images_dir / r["local"]).is_file()]
        write_sidecar(slug_dir, kept)
        log.info("images.pruned", slug=slug_dir.name, pruned=pruned)
    return pruned
