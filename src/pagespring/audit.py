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

from pagespring import manifest
from pagespring.config import cfg
from pagespring.paths import slug_dir
from pagespring.registry import pattern_by_name

log = get_logger(__name__)

Level = Literal["error", "warning"]

_LOCAL_IMG_RE = re.compile(r'(?:src=["\']|\]\()(images/[^"\')\s]+)')
# Deliberately not the localizer's matcher: a ref it declines to claim is one it
# never downloads and never counts, so borrowing its count would report clean on
# the one deliverable still remote.
_REMOTE_IMG_RE = re.compile(
    r'(?:<img\b[^>]*?\bsrc=["\']|!\[[^\]]*\]\()(https?://[^"\')\s]+)', re.IGNORECASE
)
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
    incoming_dir = slug_dir(slug)
    m = manifest.read_manifest(incoming_dir)
    if m is None:
        return [_f("manifest_missing", "error", f"no manifest.json in {incoming_dir}/")]

    deliverable = incoming_dir / m["deliverable"]
    if not deliverable.exists():
        return [_f("deliverable_missing", "error", f"{m['deliverable']} is gone — re-ingest")]
    if deliverable.stat().st_size == 0:
        return [_f("deliverable_empty", "error", f"{m['deliverable']} is 0 bytes — re-ingest")]

    findings: list[Finding] = []

    # Localize re-points refs, so a localized deliverable diverges from the staged
    # sha; `localized_sha256` is its post-localize hash. Neither present ⇒ unchecked.
    expected = m.get("localized_sha256") or (m["sha256"] if m["images"] == 0 else None)
    if expected is not None and manifest.sha256_file(deliverable) != expected:
        findings.append(
            _f("sha_mismatch", "error", "on-disk content differs from the recorded sha256")
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

    # Pages discovered but never staged. `truncated` only reports a page CAP, so
    # a crawl bled dry by throttling reports truncated=False and passes every
    # content check — the deliverable is simply missing chunks.
    lost = m.get("lost") or 0
    if lost:
        staged = m["pages"] or 0
        share = round(100 * lost / max(staged + lost, 1))
        findings.append(
            _f(
                "pages_lost",
                "error",
                f"{lost} of {staged + lost} discovered page(s) never staged ({share}%) — "
                "the source threw errors mid-crawl; re-ingest",
            )
        )

    # A crawl pattern that returned one page collapsed: the seed named a single
    # page rather than the index. Unknown pattern ⇒ unclassifiable, so stay quiet;
    # a PDF deliverable is one file however it was fetched (readthedocs serves a
    # PDF build), so kind rules it out before the pattern does. A source that IS
    # one document (a blog post, an article) says so at acquire time.
    pattern = pattern_by_name(m["pattern"])
    if (
        m["kind"] != "pdf"
        and pattern is not None
        and not getattr(pattern, "single_fetch", False)
        and not m.get("single_document")
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
        doc_text = deliverable.read_text(encoding="utf-8", errors="replace")
        # images/ existing is the fact that a localize pass ran; the count can be
        # 0 when every download failed.
        if m["images"] > 0 or (incoming_dir / "images").is_dir():
            remaining = len(set(_REMOTE_IMG_RE.findall(doc_text)))
            if remaining:
                findings.append(
                    _f(
                        "localize_incomplete",
                        "warning",
                        f"{remaining} remote image ref(s) remain — re-run localize",
                    )
                )
        # A local ref whose file is gone renders as a broken image, and no other
        # check sees it: the check above counts only REMOTE refs, so a fully
        # localized deliverable with a dead local ref audits clean.
        dangling = sorted(
            ref for ref in set(_LOCAL_IMG_RE.findall(doc_text)) if not (incoming_dir / ref).exists()
        )
        if dangling:
            findings.append(
                _f(
                    "broken_image_ref",
                    "error",
                    f"{len(dangling)} local image ref(s) point at missing files "
                    f"(e.g. {dangling[0]}) — re-ingest and re-localize",
                )
            )

        pages = m["pages"]
        if pages is not None and pages > 1:
            heading_re = _MD_HEADING_RE if m["kind"] == "markdown" else _HTML_HEADING_RE
            if not heading_re.search(doc_text):
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
    # Same source_url under two slugs is a staging error; same bytes from
    # different URLs is only suspicious.
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
