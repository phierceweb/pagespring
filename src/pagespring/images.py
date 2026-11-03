"""Image localizer: ``pw_core.fetch.images`` downloads remote images into ``images/`` and re-points
refs; this module adds pagespring's naming, fetch policy and provenance sidecar."""

from __future__ import annotations

import json
import re
import urllib.error
from collections.abc import Iterable
from html import unescape
from html.entities import html5
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urlparse

from pw_core.fetch import images as _core
from pw_core.pipeline.run_record import file_sha256
from pw_foundation.exceptions import ClientError
from pw_foundation.log import get_logger
from pw_foundation.utils.hashing import content_hash
from pw_foundation.utils.io import atomic_write_text

from pagespring import http

log = get_logger(__name__)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
# A numeric reference too long for any code point stays as written rather than decoding a prefix.
_CHAR_REF = re.compile(
    r"&(?:#\d{1,10}(?!\d);?|#[xX][0-9A-Fa-f]{1,8}(?![0-9A-Fa-f]);?|[A-Za-z][A-Za-z0-9]*;)"
)

SIDECAR_NAME = "images.json"
_SIDECAR_SCHEMA = 1
# The source is gone for good: a re-run cannot fetch it.
GONE_CODES = frozenset({404, 410})


class _Provenance(TypedDict):
    """What the fetcher saw for one image, before it is joined to a local file."""

    source_url: str
    etag: str | None
    last_modified: str | None
    bytes: int


class ImageRecord(TypedDict):
    """One localized image's provenance — what a refresh needs to skip re-downloading."""

    local: str  # filename within images/
    source_url: str
    etag: str | None
    last_modified: str | None
    sha256: str
    bytes: int


class _PacedFetcher:
    """The localizer's transport: pagespring's fetch plus the inter-image delay, and the one place
    that sees an image's URL and bytes together, so provenance is captured here."""

    def __init__(self, taken: Iterable[str] = ()) -> None:
        # (sha256, fields, file): two URLs can yield identical bytes, so each fetch keeps its own.
        self.fetched: list[tuple[str, _Provenance, str | None]] = []
        # decoded URL -> whether this run's fetch of it answered a GONE_CODES status
        self.gone: dict[str, bool] = {}
        self._taken = set(taken)

    def get_bytes(self, url: str, *, timeout_s: float | None = None) -> tuple[str, bytes]:
        """Fetch on ``fetch_bytes``' long budget, decoding the HTML-escaped ref: a CDN reads
        ``amp;wid`` as an unknown parameter and serves its default rendition."""
        decoded = decode_ref(url)
        try:
            final_url, data, meta = http.fetch_bytes_meta(decoded)
        except urllib.error.HTTPError as exc:
            self.gone[decoded] = exc.code in GONE_CODES
            raise
        except ValueError as exc:
            self.gone[decoded] = False
            # The localizer skips only the errors it knows; any other one would end the pass.
            raise ClientError("image fetch failed", context={"url": decoded}, cause=exc) from exc
        except Exception:
            self.gone[decoded] = False
            raise
        self.gone[decoded] = False
        fields: _Provenance = {
            "source_url": decoded,
            "etag": meta["etag"],
            "last_modified": meta["last_modified"],
            "bytes": len(data),
        }
        self.fetched.append((content_hash(data), fields, self._claim(url, data)))
        http.polite_sleep()
        return final_url, data

    def _claim(self, url: str, data: bytes) -> str | None:
        """The file the localizer saves these bytes as, claimed as it claims it: the proposed name
        (sniffed extension added when it has none), else the first free ``stem-N``."""
        sniffed = _core.sniff_image_ext(data)
        if sniffed is None:
            return None  # the localizer refuses a body that is not an image
        base = local_name(url)
        name = base if Path(base).suffix.lower() in _core._IMAGE_EXTENSIONS else base + sniffed
        stem, ext, n = Path(name).stem, Path(name).suffix, 2
        while name in self._taken:
            name, n = f"{stem}-{n}{ext}", n + 1
        self._taken.add(name)
        return name


_MD_ANY_SCHEME_RE = re.compile(r"\]\(((?i:https?)://[^)\s]++)(?=" + _core._MD_REF_END + ")")


def remote_image_urls(doc_path: Path) -> list[str]:
    """The remote image URLs in ``doc_path`` in first-seen order, by the localizer's own matcher, so
    it always agrees with ``count_remote_images``."""
    return remote_image_refs(doc_path.read_text(encoding="utf-8"))


def remote_image_refs(text: str) -> list[str]:
    """``remote_image_urls`` of a document already read."""
    return [url for url, _fetch in _core._targets(text, None)]


def remote_image_starts(text: str) -> set[int]:
    """Offsets of the remote refs an image pass re-points: pw-core's finders' matches, and any markdown
    target its rewrite also takes because it names one of those refs."""
    starts = {
        match.start(1)
        for regex in (_core._MD_REMOTE_RE, _core._HTML_REMOTE_RE)
        for match in regex.finditer(text)
        if _core._is_image_url(match[1])
    }
    refs = set(remote_image_refs(text))
    starts.update(m.start(1) for m in _MD_ANY_SCHEME_RE.finditer(text) if m[1] in refs)
    return starts


def _read_payload(slug_dir: Path) -> dict[str, Any]:
    path = slug_dir / SIDECAR_NAME
    if not path.is_file():
        return {}
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        log.warning("images.sidecar_unreadable", path=str(path))
        return {}
    return data if isinstance(data, dict) else {}


def read_sidecar(slug_dir: Path) -> list[ImageRecord]:
    """Per-image provenance for ``incoming/<slug>/``; empty when absent."""
    records: list[ImageRecord] = _read_payload(slug_dir).get("images", [])
    return records


def read_gone(slug_dir: Path) -> list[str]:
    """Decoded URLs of still-remote refs whose last fetch answered 404 or 410."""
    gone = _read_payload(slug_dir).get("gone", [])
    return [url for url in gone if isinstance(url, str)] if isinstance(gone, list) else []


def write_sidecar(
    slug_dir: Path, records: list[ImageRecord], *, gone: list[str] | None = None
) -> None:
    """Atomic: a torn sidecar orphans every image it failed to record. ``gone`` defaults to the
    marks already there."""
    gone = read_gone(slug_dir) if gone is None else gone
    payload: dict[str, Any] = {"schema_version": _SIDECAR_SCHEMA, "images": records}
    if gone:
        payload["gone"] = sorted(gone)
    slug_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        slug_dir / SIDECAR_NAME, json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _merge(old: list[ImageRecord], new: list[ImageRecord]) -> list[ImageRecord]:
    """New records win per source_url and per file; a resumed run must not drop earlier passes."""
    taken = {r["local"] for r in new}
    merged = {r["source_url"]: r for r in old if r["local"] not in taken}
    merged.update({r["source_url"]: r for r in new})
    return sorted(merged.values(), key=lambda r: r["source_url"])


def _decode_match(match: re.Match[str]) -> str:
    ref = match.group()
    # html.unescape prefix-matches an unknown name (``&copy2;`` -> ``©2;``); an attribute does not.
    return unescape(ref) if ref[1] == "#" or ref[1:] in html5 else ref


def decode_ref(url: str) -> str:
    """Decode an image ref's numeric references and its named ones that end in ``;``; any other
    ``&``, such as a bare ``&reg=`` or an unknown ``&copy2;``, stays as written."""
    return _CHAR_REF.sub(_decode_match, url)


def local_name(url: str) -> str:
    """Local name for a remote image ref: its decoded URL's sanitized, lowercased basename stem, its
    image extension when it has one (else the localizer sniffs one), and a digest of any query."""
    decoded = decode_ref(url)
    path = urlparse(decoded).path
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", Path(path).name)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-").lower() or "image"
    suffix = Path(path).suffix.lower()
    ext = ".jpg" if suffix == ".jpeg" else (suffix if suffix in _IMAGE_EXTS else "")
    return _core.with_query_digest(f"{stem}{ext}", decoded)


def gone_remote_images(doc_path: Path, slug_dir: Path) -> int:
    """Distinct remote refs in ``doc_path`` whose source was last found gone; a re-run cannot fetch
    them. Counted as ``count_remote_images`` counts."""
    gone = set(read_gone(slug_dir))
    return sum(1 for url in remote_image_urls(doc_path) if decode_ref(url) in gone)


def count_remote_images(doc_path: Path) -> int:
    """Distinct remote image refs still in ``doc_path`` (0 ⇒ fully localized) — lets
    a caller know whether another ``download_images`` pass is needed."""
    return _core.count_remote_images(doc_path.read_text(encoding="utf-8"))


def download_images(doc_path: Path, images_dir: Path, *, checkpoint_every: int = 50) -> int:
    """Download remote images into ``images_dir``, re-pointing each ref as it lands so a killed run
    keeps its work; returns this run's count. Re-run until only gone refs remain."""
    # A file already here is not one of this run's downloads, whatever it hashes to, and the
    # localizer claims new names around it. Read from the directory, not the sidecar.
    preexisting = (
        {p.name for p in images_dir.glob("*") if p.is_file()} if images_dir.is_dir() else set()
    )
    fetcher = _PacedFetcher(taken=preexisting)
    try:
        return _core.localize_file(
            doc_path,
            images_dir,
            checkpoint_every=checkpoint_every,
            fetcher=fetcher,
            namer=local_name,
            reuse_existing=False,
        )
    finally:
        # An interrupted run keeps its checkpointed local refs, so it keeps their records too.
        if fetcher.fetched:
            _record_provenance(images_dir, fetcher.fetched)
        _record_gone(doc_path, images_dir.parent, fetcher.gone)


def _record_gone(doc_path: Path, slug_dir: Path, answered: dict[str, bool]) -> None:
    """Mark each ref this run found gone, clear the ones it fetched or that failed otherwise, and
    drop marks for refs no longer remote."""
    old = set(read_gone(slug_dir))
    remote = {decode_ref(url) for url in remote_image_urls(doc_path)}
    kept = {url for url in old if answered.get(url, True)}
    gone = (kept | {url for url, was_gone in answered.items() if was_gone}) & remote
    if gone != old:
        write_sidecar(slug_dir, read_sidecar(slug_dir), gone=sorted(gone))


def _record_provenance(
    images_dir: Path, fetched: list[tuple[str, _Provenance, str | None]]
) -> None:
    """Record each download with the file it was saved as, when that file holds its bytes: a refetch
    writes into the recorded file, so a guessed pairing would overwrite another ref's figure."""
    slug_dir = images_dir.parent
    records: list[ImageRecord] = []
    for digest, fields, local in fetched:
        if local is None:
            continue
        path = images_dir / local
        if not path.is_file() or file_sha256(path) != digest:
            continue
        records.append(
            {
                "local": local,
                "source_url": fields["source_url"],
                "etag": fields["etag"],
                "last_modified": fields["last_modified"],
                "sha256": digest,
                "bytes": fields["bytes"],
            }
        )
    write_sidecar(slug_dir, _merge(read_sidecar(slug_dir), records))
