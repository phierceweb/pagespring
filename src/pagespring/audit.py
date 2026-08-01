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
from pagespring.registry import pattern_by_name

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

    # A page cap cut the crawl short, so the deliverable is partial. Nothing about
    # the content shows it — when the source grew between versions, the truncated
    # copy is still bigger than the last one and every other check passes.
    if m.get("truncated"):
        findings.append(
            _f(
                "crawl_truncated",
                "error",
                f"crawl hit its page cap at {m['pages']} pages — the deliverable is partial",
            )
        )

    # A crawl pattern that returned one page collapsed: the seed named a single
    # page rather than the index. Unknown pattern ⇒ unclassifiable, so stay quiet;
    # a PDF deliverable is one file however it was fetched (readthedocs serves a
    # PDF build), so kind rules it out before the pattern does.
    pattern = pattern_by_name(m["pattern"])
    if (
        m["kind"] != "pdf"
        and pattern is not None
        and not getattr(pattern, "single_fetch", False)
        and m["pages"] == 1
    ):
        findings.append(
            _f(
                "single_page_crawl",
                "error",
                f"{m['pattern']} yielded 1 page — seed URL likely names a page, not the index",
            )
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


def _corpus_findings(slugs: list[str]) -> dict[str, list[Finding]]:
    """Checks that need the WHOLE corpus, keyed by the slug they attach to.

    The defect exists only in the relation between two slugs, so no per-slug
    check can reach it. Derived fresh from the manifests rather than persisted: a duplicate may be
    ingested *after* the slug it collides with, so nothing written at ingest
    time can be trusted to still be complete.
    """
    incoming = Path(cfg.INCOMING_DIR)
    by_sha: dict[str, list[str]] = {}
    by_url: dict[str, list[str]] = {}
    for slug in slugs:
        m = manifest.read_manifest(incoming / slug)
        if m is None:
            continue
        by_sha.setdefault(m["sha256"], []).append(slug)
        by_url.setdefault(m["source_url"], []).append(slug)

    out: dict[str, list[Finding]] = {}
    # Same URL under two slugs is an error, not noise: pagespeak names its output
    # after the source file, so the second claim deadlocks the hand-off and BOTH
    # slugs get skipped. Same bytes from different URLs is only suspicious.
    checks: tuple[tuple[dict[str, list[str]], str, Level, str], ...] = (
        (by_sha, "duplicate_content", "warning", "byte-identical content"),
        (by_url, "duplicate_source_url", "error", "the same source_url"),
    )
    for group, check, level, what in checks:
        for members in group.values():
            if len(members) < 2:
                continue
            for slug in members:
                others = ", ".join(s for s in members if s != slug)
                out.setdefault(slug, []).append(_f(check, level, f"{what} as: {others}"))
    return out


def audit_all() -> list[tuple[str, list[Finding]]]:
    """Audit every ``incoming/<slug>/`` in sorted order, plus corpus-wide checks."""
    incoming = Path(cfg.INCOMING_DIR)
    slugs = sorted(p.name for p in incoming.glob("*") if p.is_dir()) if incoming.is_dir() else []
    corpus = _corpus_findings(slugs)
    return [(s, audit_slug(s) + corpus.get(s, [])) for s in slugs]
