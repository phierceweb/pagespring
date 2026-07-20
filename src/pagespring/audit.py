"""audit — $0 deterministic checks over staged deliverables.

Read-only (no network, no LLM): each check compares what the manifest claims
against what's actually on disk, so a half-lost crawl, a hand-edited file, or
an unfinished localize surfaces as a finding instead of flowing silently into
pagespeak. Error-level findings mean the deliverable can't be trusted;
warnings are real-but-survivable RAG noise.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, TypedDict

from pf_core.log import get_logger

from pagespring import images, manifest
from pagespring.config import cfg

log = get_logger(__name__)

Level = Literal["error", "warning"]

_MD_HEADING_RE = re.compile(r"^#{1,6} ", re.MULTILINE)
_HTML_HEADING_RE = re.compile(r"<h[1-6][\s>]", re.IGNORECASE)


class Finding(TypedDict):
    """One defect: which check fired, how bad, and what it saw."""

    check: str
    level: Level
    detail: str


def _f(check: str, level: Level, detail: str) -> Finding:
    return {"check": check, "level": level, "detail": detail}


def audit_slug(slug: str) -> list[Finding]:
    """Audit one ``incoming/<slug>/``; empty list ⇒ healthy."""
    incoming_dir = Path(cfg.INCOMING_DIR) / slug
    m = manifest.read_manifest(incoming_dir)
    if m is None:
        return [_f("manifest_missing", "error", f"no manifest.json in incoming/{slug}/")]

    deliverable = incoming_dir / m["deliverable"]
    if not deliverable.exists():
        return [_f("deliverable_missing", "error", f"{m['deliverable']} is gone — re-ingest")]
    if deliverable.stat().st_size == 0:
        return [_f("deliverable_empty", "error", f"{m['deliverable']} is 0 bytes — re-ingest")]

    findings: list[Finding] = []

    # Only un-localized files must hash to the staged sha — localize (images>0)
    # re-points refs, so its bytes legitimately diverge.
    if m["images"] == 0 and manifest.sha256_file(deliverable) != m["sha256"]:
        findings.append(
            _f("sha_mismatch", "error", "on-disk content differs from the staged sha256")
        )

    if m["kind"] in ("markdown", "html"):
        if m["images"] > 0:
            remaining = images.count_remote_images(deliverable)
            if remaining:
                findings.append(
                    _f(
                        "localize_incomplete",
                        "warning",
                        f"{remaining} remote image ref(s) remain — re-run localize",
                    )
                )
        pages = m["pages"]
        if pages is not None and pages > 1:
            heading_re = _MD_HEADING_RE if m["kind"] == "markdown" else _HTML_HEADING_RE
            text = deliverable.read_text(encoding="utf-8", errors="replace")
            if not heading_re.search(text):
                findings.append(
                    _f(
                        "no_headings",
                        "warning",
                        f"{pages} pages normalized to zero headings — splits into nothing",
                    )
                )

    return findings


def audit_all() -> list[tuple[str, list[Finding]]]:
    """Audit every ``incoming/<slug>/`` in sorted order."""
    incoming = Path(cfg.INCOMING_DIR)
    slugs = sorted(p.name for p in incoming.glob("*") if p.is_dir()) if incoming.is_dir() else []
    return [(s, audit_slug(s)) for s in slugs]
