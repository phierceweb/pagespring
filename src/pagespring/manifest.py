"""The per-slug ``manifest.json`` — the provenance record written beside each
``incoming/<slug>/`` deliverable.

It records where a manual came from, which pattern acquired it, the downstream
pagespeak ``convert_recipe`` hint, and a content hash — so the hand-off to
pagespeak is self-describing, and so ``ingest --if-changed`` can tell whether a
re-fetch produced anything new. Pure stdlib; no network, no pattern machinery.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TypedDict

from pagespring import __version__

MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION = 1


class Manifest(TypedDict):
    """The on-disk shape of ``incoming/<slug>/manifest.json`` (schema v1)."""

    schema_version: int
    pagespring_version: str
    source_url: str
    pattern: str
    slug: str
    kind: str
    deliverable: str
    convert_recipe: list[str]
    pages: int | None
    bytes: int
    sha256: str
    images: int
    ingested_at: str


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of ``path``'s bytes (the deliverable's content identity)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(
    *,
    source_url: str,
    pattern: str,
    slug: str,
    kind: str,
    deliverable: str,
    convert_recipe: list[str],
    pages: int | None,
    size_bytes: int,
    sha256: str,
    images: int,
    ingested_at: str,
) -> Manifest:
    """Assemble a manifest from one ingest's facts (stamps schema + version)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "pagespring_version": __version__,
        "source_url": source_url,
        "pattern": pattern,
        "slug": slug,
        "kind": kind,
        "deliverable": deliverable,
        "convert_recipe": convert_recipe,
        "pages": pages,
        "bytes": size_bytes,
        "sha256": sha256,
        "images": images,
        "ingested_at": ingested_at,
    }


def write_manifest(slug_dir: Path, manifest: Manifest) -> Path:
    """Write ``manifest`` as pretty JSON to ``slug_dir/manifest.json``; return it."""
    path = slug_dir / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def read_manifest(slug_dir: Path) -> Manifest | None:
    """Read ``slug_dir/manifest.json``; ``None`` if absent or unparseable.

    Tolerant by design: a legacy slug dir (pre-manifest) or a corrupt file must
    not crash ``status`` or ``--if-changed`` — they treat ``None`` as "no record".
    """
    path = slug_dir / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        data: Manifest = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data
