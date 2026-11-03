"""Upkeep of a slug's ``images/`` cache around a download pass: case-fold names, reuse what the
server still serves unchanged, prune what the deliverable no longer references."""

from __future__ import annotations

import urllib.error
from pathlib import Path

from pw_core.fetch import images as _core
from pw_core.pipeline.run_record import file_sha256
from pw_foundation.log import get_logger
from pw_foundation.utils.hashing import content_hash
from pw_foundation.utils.io import atomic_write_bytes, atomic_write_text

from pagespring import http
from pagespring.images import (
    GONE_CODES,
    ImageRecord,
    decode_ref,
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
    """Re-point refs at sidecar images the server still serves unchanged, or no longer serves at all;
    returns how many settled. Otherwise new bytes replace the file in place, or file and ref stay."""
    records = {r["source_url"]: r for r in read_sidecar(slug_dir)}
    if not records:
        return 0
    images_dir = slug_dir / "images"
    text = doc_path.read_text(encoding="utf-8")
    reused = refreshed = 0
    for url in remote_image_urls(doc_path):
        # Probe the decoded URL the sidecar keys on (an `&amp;` ref never matches its validators);
        # the rewrite below must still match the escaped form the document holds.
        decoded = decode_ref(url)
        rec = records.get(decoded)
        if rec is None:
            continue
        cached = images_dir / rec["local"]
        # A pass killed before it recorded can leave this record on a file now holding another image.
        if not cached.is_file() or file_sha256(cached) != rec["sha256"]:
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
                # Saved now: a pass killed later must not leave the new bytes under the old hash.
                write_sidecar(slug_dir, sorted(records.values(), key=lambda r: r["source_url"]))
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
    """Fetch ``url`` and, when its bytes still belong under ``rec["local"]``, write them there and
    update ``rec``; True leaves both untouched when the source is gone, False when it failed."""
    try:
        _final, data, meta = http.fetch_bytes_meta(url)
    except urllib.error.HTTPError as exc:
        # Gone for good, so the ref settles on its cached copy instead of retrying.
        if exc.code in GONE_CODES:
            log.info("images.source_gone", url=url, code=exc.code, local=rec["local"])
            return True
        log.warning("images.refetch_failed", url=url, error=str(exc))
        return False
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


def _settleable(doc_path: Path, slug_dir: Path) -> set[str]:
    """Files the sidecar records for a remote ref still in the document: the files it can settle on."""
    wanted = {decode_ref(url) for url in remote_image_urls(doc_path)}
    return {r["local"] for r in read_sidecar(slug_dir) if r["source_url"] in wanted}


def prune_orphans(doc_path: Path, slug_dir: Path) -> int:
    """Delete images the document no longer references (the sidecar can miss a replaced file), and
    their records, keeping any file a ref that is still remote could settle on."""
    images_dir = slug_dir / "images"
    if not images_dir.is_dir():
        return 0

    text = doc_path.read_text(encoding="utf-8")
    settleable = _settleable(doc_path, slug_dir)
    pruned = 0
    for path in sorted(images_dir.glob("*")):
        if not path.is_file() or f"images/{path.name}" in text or path.name in settleable:
            continue
        path.unlink(missing_ok=True)
        pruned += 1
    if pruned:
        kept = [r for r in read_sidecar(slug_dir) if (images_dir / r["local"]).is_file()]
        write_sidecar(slug_dir, kept)
        log.info("images.pruned", slug=slug_dir.name, pruned=pruned)
    return pruned
