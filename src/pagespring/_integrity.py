"""Whether a staged deliverable is still the file its manifest records."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, TypeGuard

from pf_core.exceptions import PreconditionError
from pf_core.log import get_logger

from pagespring import manifest

log = get_logger(__name__)

Integrity = Literal["intact", "damaged", "unverifiable"]

LOCAL_IMG_RE = re.compile(r'(?:src=["\']|\]\()(images/[^"\')\s]+)')

# read_manifest returns any parseable JSON as-is, so a truncated file reaches
# callers missing the fields they index.
REQUIRED_FIELDS = ("source_url", "pattern", "kind", "deliverable", "pages", "sha256", "images")


def usable(m: object) -> TypeGuard[manifest.Manifest]:
    return isinstance(m, dict) and all(k in m for k in REQUIRED_FIELDS)


def image_pass_ran(slug_dir: Path, m: manifest.Manifest, doc_text: str) -> bool:
    """Whether an image pass may have re-pointed refs: images>0 (0 after a kill
    mid-pass), or images/ exists *and* the file still carries a local ref (a
    re-ingest keeps the dir)."""
    return m.get("images", 0) > 0 or (
        (slug_dir / "images").is_dir() and bool(LOCAL_IMG_RE.search(doc_text))
    )


def integrity(slug_dir: Path, m: manifest.Manifest) -> Integrity:
    """How the file on disk stands against ``m``: ``localized_sha256`` once an image
    pass recorded one, else ``sha256``. A file an image pass re-pointed without
    recording a hash has nothing to be compared with: unverifiable, not damaged."""
    name = m.get("deliverable")
    if not isinstance(name, str) or not name:
        return "damaged"
    path = slug_dir / name
    if not path.is_file() or not path.stat().st_size:
        return "damaged"
    actual = manifest.sha256_file(path)
    if m.get("localized_sha256"):
        return "intact" if actual == m.get("localized_sha256") else "damaged"
    if actual == m.get("sha256"):
        return "intact"
    if m.get("kind") == "pdf" or not (slug_dir / "images").is_dir():
        return "damaged"
    # Only re-pointed refs can explain the mismatch; a file without any is not the one
    # an image pass left.
    doc_text = path.read_text(encoding="utf-8", errors="replace")
    return "unverifiable" if LOCAL_IMG_RE.search(doc_text) else "damaged"


def deliverable_intact(slug_dir: Path, m: manifest.Manifest) -> bool:
    """Whether the staged file may stand: intact, or unverifiable (re-staging would
    undo its localized refs, whose remote URLs may no longer resolve)."""
    return integrity(slug_dir, m) != "damaged"


def read_usable(slug_dir: Path) -> manifest.Manifest:
    """``slug_dir``'s manifest, refused with ``PreconditionError`` when absent or unusable."""
    m = manifest.read_manifest(slug_dir)
    if m is None:
        raise PreconditionError(f"no manifest for {slug_dir}/ — ingest it first")
    if not usable(m):
        raise PreconditionError(
            f"manifest.json in {slug_dir}/ is missing required fields — re-ingest"
        )
    return m


def open_for_image_pass(slug_dir: Path, m: manifest.Manifest) -> None:
    """Refuse an image pass over a deliverable that no longer matches its record,
    then drop ``localized_sha256`` for the length of the pass.

    The pass records the file's hash as verified, so it must not run over damage.
    A pass that ran without recording a hash left nothing to compare: warn, proceed.
    The pass checkpoints the file as images land, so a kill leaves bytes no record
    describes — unrecorded, the re-run resumes instead of refusing.
    """
    state = integrity(slug_dir, m)
    deliverable = slug_dir / m["deliverable"]
    if state == "damaged":
        raise PreconditionError(
            f"{deliverable} no longer matches the hash its manifest records — re-ingest "
            f"{slug_dir.name}; localize would record the damaged file as verified"
        )
    if state == "unverifiable":
        log.warning("localize.unverifiable", slug=slug_dir.name, deliverable=str(deliverable))
    if m.get("localized_sha256"):
        m["localized_sha256"] = None
        manifest.write_manifest(slug_dir, m)
